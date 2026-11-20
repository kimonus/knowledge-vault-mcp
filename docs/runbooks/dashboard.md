# Optional Knowledge Dashboard

The separate `dashboard/` Python package implements ADR 0009's browser security boundary.
It is disabled by default. The React/TypeScript Explorer and Portal ship in a separate image.
The Helm chart conditionally creates its Deployment, ClusterIP Service, ServiceAccount,
ConfigMap and NetworkPolicies; TLS Ingress is an additional opt-in. Existing core HTTP/MCP
clients and readiness do not depend on this process. Reading this runbook does not authorize
live deployment, Secret provisioning, ingress, DNS, Cloudflare or tunnel changes.

## Runtime and authority

The BFF imports FastAPI, httpx, JSON Schema and PyJWT, with the pure HMAC/Host/Access helpers
maintained under `src/knowledge_vault/auth`. Relative source symlinks in the dashboard package
reuse those helpers; the built wheel contains regular Python files and works without core source.
Core builds remain independent of `dashboard/`. No core Container, PostgreSQL client, model
loader, embedding package, database URL, core pepper or bootstrap records enter the BFF runtime.

The core connection uses a fixed operator-configured Service URL and private core Host.
HTTP URLs must end in `.svc.cluster.local`; use verified HTTPS for any other connection.
The BFF holds a separately issued inspection bearer. The core validates its exact-read scopes
and designated inspection principal before serving `/api/v1/read`. The BFF checks protocol `1`
and the complete required operation allowlist before **each** data call, so core credential
revocation/reconfiguration fails closed. It never falls back to ordinary core routes.
Opaque bearer scopes cannot be verified locally without the forbidden core pepper/records;
the authenticated capability request is the authority check at the core boundary.

Browser data paths are exactly the named inspection operations under `/dashboard/api/v1`,
excluding core-only `capabilities`. See [inspection API](../http-inspection.md) for data shapes.
`GET /session`, `POST /session/login` and `POST /session/logout` are BFF-only operations.
`GET /health/live` returns 204; readiness returns 204 only when enabled with a compatible core.
Disabled readiness is 503, and all browser routes return 404. Probes have no browser identity.
Readiness dependency work has a separate 60/minute process-wide admission budget, independent
of Host/client headers and browser budgets. Cache hits do not consume this budget or turn a
known healthy Pod unready after an HTTP burst. Results are cached for five seconds, including failures.
Only one uncached readiness check may run; concurrent probes fail promptly with safe 503 rather
than queue. Its two-second absolute deadline includes waiting for one of the four total upstream
slots. Thus probes can occupy at most one slot, and a burst cannot multiply authenticated core
checks or drain browser admission. Cancellation does not cache a false success. Liveness does
no dependency work. Future ingress must keep probe paths internal.
Only explicitly named dashboard document routes and built `.js`/`.css` files are served.
There is no API/docs/static/SPA catch-all, CORS, redirect or generic proxy.

Input/output schemas are generated from PR1 with `scripts/export_dashboard_contract.py`;
CI checks drift. Private input text stays in JSON bodies. Query parameters are accepted only for
bounded child paging and artifact offsets; no caller-selected URL, headers, scope or purpose.
Unknown fields, repeated parameters and encoded/dot/double-slash paths are refused.
Sessions, login credentials and browser headers are never forwarded upstream.

## Configuration and Secret references

The entrypoint is `knowledge-vault-dashboard [--config /path/to/public-config.json]` on port 8080.
Without a config it serves only disabled probes. It never reads `.env` or core environment settings.
The JSON config has `enabled` (default false), `host`, `core_url`, `core_host`, `mode`
(`privateSession` by default), and `service_token_file`. These names describe local file references
for read-only Kubernetes Secret mounts. Helm references existing Secrets; it never creates them.
Direct `service_token`, `pepper`, `signing_key` and `records` values in that config are rejected.

Private mode also requires `pepper_file`, `signing_key_file`, and `records_file`. The first two
hold different random keys of at least 32 characters. Record JSON uses the existing token record
shape (`principal_id`, HMAC-SHA256 `sha256`, `scopes`), with **only** `knowledge:read` on every
record. Browser credentials must be different from the service bearer and must never be registered
at core. Generate records through the existing offline token workflow with an explicit read-only
scope; its usual write-capable defaults are unsuitable. Never mount the core token pepper/records,
DB Secret, writer/admin bearer or model volume into the dashboard.

The hostname must differ from the private core Host and both MCP
hostnames. The BFF validates exactly one syntactically valid Host with canonical case/port/trailing
dot handling. Origins are compared exactly to `https://` plus the configured dashboard host;
scheme, sibling domain and port differences are rejected. Forwarding headers never select identity,
origin or rate-limit address. The entrypoint disables Uvicorn proxy headers and access logs,
and suppresses httpx/httpcore URL logging. An alternative launcher must preserve these settings.

Access mode instead requires `access_issuer`, `access_audience`, and `access_emails`. Issuer must
be a dedicated HTTPS `*.cloudflareaccess.com` origin. Private records/keys are refused in this
mode. Every browser request requires an RS256 assertion verified for signature, issuer, audience,
issue/expiry times and exact allowed email, projected to READ. No app cookie, private login,
bearer fallback or callback listener is used. JWT key fetching has a five-second absolute deadline,
128 KB response bound, redirect refusal, cached keys and a 30-second refresh throttle.
This configuration does not create an Access application, route or DNS record. Those require
separate operator authorization and review under the [Access runbook](cloudflare-access.md).

## Browser session protocol

Private login accepts only `{credential}` JSON with exact HTTPS Origin and
`X-Dashboard-Request: 1`. Cross-site and sibling-site Fetch Metadata are refused.
Login credentials are opaque ASCII tokens, at most 512 characters, never placed in a URL.
The client must keep them out of JavaScript storage and clear the login input after exchange.

The `__Host-kv_dashboard` cookie is Secure, HttpOnly, SameSite=Strict, Path=/, without Domain.
Its HS256 payload contains only principal, audience, issue/expiry, random nonce and an opaque
keyed reference to the matched credential record. Absolute lifetime is 1,800 seconds with no
renewal. Cookies over 4 KB and malformed/nested JWT payloads are refused. Every request verifies
signature, audience, expiry and the still-configured exact-read
record. Token digests and raw credentials never enter the payload.

`GET /session` returns identity, integer Unix expiry, capabilities and session-bound CSRF token.
It also returns the core's validated public `max_page_size`, with the capability call sharing
the four-slot upstream budget and absolute timeout. An unavailable core can therefore make
session bootstrap temporarily unavailable; retry does not require replacing browser credentials.
The client respects this cap for every page/history request. Unpaged details/context/aliases
and artifact character offsets do not consume a page-size allowance.
All POSTs require JSON, exact Origin and the custom header; logout additionally requires
`X-CSRF-Token` and `{}`. Access CSRF is bound to the verified assertion and its expiry.
BFF-originated API errors are JSON and do not redirect to a login page. Access can reject an
expired provider session at the edge before the BFF sees the request; the edge response is not
the BFF ProblemDetail contract. The same-origin fetch client sends
`credentials: "same-origin"` and `X-Requested-With: XMLHttpRequest` in addition to the BFF's
required headers. Cloudflare documents that AJAX header for expired-session 401 behavior.
The client must also handle non-JSON 401/403, an edge login redirect or fetch/network failure as
an authentication failure, clear private in-memory state, and offer top-level reauthentication.
It must never render returned edge HTML as application content. See
[Cloudflare session management](https://developers.cloudflare.com/cloudflare-one/access-controls/access-settings/session-management/).
The real-provider procedure below tests an actually expired Access browser session through the
configured edge, including its response format and reauthentication flow. Origin tests cannot
prove that behavior.
Knowledge is returned only as JSON untrusted data;
CSP disallows inline/eval/remote scripts, objects, framing and remote connections. Responses
include no-store, nosniff, no-referrer, restrictive Permissions-Policy and HSTS.
Component and Chromium/Firefox tests verify inert rendering and actual browser CSP enforcement.

Remove a browser record or rotate the signing key and roll out **only BFF** to revoke sessions.
Sign out immediately unmounts private views and aborts reads, before a separate CSRF-aware
logout exchange completes. That exchange has a five-second client deadline; login remains
blocked until it finishes or times out, and a late refresh cannot restore the old session tree.
Failure is reported as unconfirmed server logout after local data clearing.
Unchanged settings preserve sessions across restart. Overlapping records preserve sessions of
the still-enabled record. Logout deletes the local cookie; a copied cookie remains valid until
expiry or this coarse revocation. Access logout is local data clearing only: provider logout
and revocation belong to Access. No server-side session/revocation database is claimed.
Rotate the BFF-to-core service bearer separately using overlapping core inspection records,
then a BFF-only Secret rollout, then retirement of the old core record.

## Limits and failures

Requests have a 128 KB body cap and ten-second upload deadline, including chunked bodies.
The core client has four concurrent slots, a two-second connect/pool timeout and a 60-second
absolute budget including queueing, capability checking and streamed JSON. Responses are capped
at 2 MB; compressed bodies, redirects, invalid schemas, duplicate JSON members and non-JSON
numeric constants are refused. No automatic retry or direct database fallback exists.
Browser reads have 120 requests/minute per principal; login has 10/minute per trusted socket
address and 30/minute globally. A 600/minute aggregate budget and 1,024-entry LRU bound memory.
These process-local single-replica budgets reset on restart. Behind an ingress the socket address
can be shared, making the login limit stricter; spoofed forwarding cannot bypass it.

Upstream service 401/403 and dependency failures become safe 503; timeouts are 504; invalid
responses are 502. Core 404/422 preserve only the status with a fixed error code, never upstream
payloads. Core 429 accepts only a bounded numeric Retry-After. No Set-Cookie, Location or
arbitrary upstream headers are relayed. Errors contain no credentials, URLs, query text or
upstream exception messages. A JSON response echoing the service bearer is rejected.
The BFF does not log knowledge, headers or bodies. Its failure affects only dashboard availability.

## Verification

From the repository root:

```bash
uv sync --locked
uv run python scripts/export_dashboard_contract.py --check
uv lock --check --project dashboard
uv sync --locked --project dashboard --no-editable
cd dashboard
.venv/bin/pyright
.venv/bin/pytest --cov=knowledge_vault_dashboard --cov-branch --cov-report=json:coverage.json -q
.venv/bin/python ../scripts/check_coverage.py coverage.json
cd ..
uv run pytest tests/contract/test_dashboard_core_boundary.py -q
```

Dashboard tests use synthetic records, fake upstream transports and local RSA keys. The last
test uses real disposable pgvector and the actual core ASGI stack; it verifies inspection reads,
no accounting changes, service/browser separation and existing writer functionality.
No test loads embedding weights or reads local `.env`. All-sensitivity access, live pagination,
coarse revocation, and a single process remain accepted limits. Independent security review,
deployment/client proof remains required before production enablement. See the
[PR3 verification record](../reviews/web-ui-pr3.md) for coverage and remaining review requirements.

## Explorer and Portal

The Explorer defaults to **All retained**, including every status. Exact text browsing also
uses exhaustive keyset pages; ranked search is explicitly a bounded candidate set. Filters cover
topics, context IDs, kinds, status, sensitivity, evidence, workflow, origins, temporal role,
confidence and available dates. Unclassified topic/context/facet filters apply to exhaustive
browsing, not ranked search. Reset and applying new filters reset the cursor. Explorer retains
the applied view while draft fields are edited; edits alone send no knowledge query. Previous
pages retain at most 100 cursor boundaries in session memory. A live scan is not a snapshot: concurrent
changes can affect membership and order. Always follow the returned continuation even when a
byte-limited page contains fewer rows than requested.

Details display complete assertion text, stored metadata, source provenance, paged sources,
artifact references/text chunks, relationships in both directions and temporal identities.
An absent timestamp or unavailable usage is shown explicitly; the UI never invents a modified
time. Source links accept only HTTP(S), open separately with no referrer/opener and never fetch
previews. Markdown, HTML, source locators and artifacts are plain inert text. Sensitivity is
visible but is not a redaction boundary: this is a single-user, all-sensitivity reader.

Portal navigation comes from stored topic/context/facet catalogs, counts and aliases. Topic
and context pages link directly to Explorer records. Facts, decisions, proposals, considered and
rejected options remain distinct; status and historical role remain explicit independent filters.
Ingestion confirmation is not epistemic verification. No LLM, summaries or inferred links exist.
Temporal views use exact identity, applicable `as_of` and receipt `known_at` cutoffs. Unknown,
ambiguous and conflicting outcomes remain unresolved. Resolver overflow offers independently
paged immutable raw evidence, including more than 1,000 entries, without selecting a partial winner.

Filter/cursor/facet state exists only in the authenticated React tree. Logout or auth expiry
unmounts it and aborts outstanding requests. There is no local/session storage, service worker,
source map or embedded knowledge corpus in production assets. URLs contain only record/context
IDs and encoded topic resource names, never search text, credential or temporal values. Topics
are UTF-8 base64url path segments, an encoding rather than encryption; history still reveals
which topic was visited. A reload starts a new filter view.

## Frontend development and packaging

Use Node 24.15.0 and locked dependencies. From `web/`, run:

```bash
npm ci
npm run check:contract
npm run typecheck
npm run lint
npm run format:check
npm test
npm run build
npx playwright install --with-deps --no-shell chromium firefox
npm run test:browser
```

Install both Python projects with `uv sync --locked` and `uv sync --locked --project dashboard`
first. Browser tests bind only loopback HTTPS with ephemeral certificates; they require Docker
for a disposable digest-pinned pgvector database. One suite uses actual BFF/schema validation
and a large synthetic upstream; another runs the actual core inspection services and database.
The Access edge is simulated with real local RS256 signatures. It is not proof of live provider
session expiry. No test reads operator credentials, `.env`, live data or model weights.

`npm run generate` regenerates TypeScript from the BFF contract exported by the core. `build`
copies only compiled index/JS/CSS into the ignored Python package `static/` directory. Build
before building the Python wheel if browser assets are wanted in that wheel. API-only wheels
remain valid and return 404 for absent browser assets. Vite's dev server is only a local asset
tool; authenticated integration uses the HTTPS BFF. Never expose the Vite dev server publicly.

Build the independent image from the repository root with a BuildKit-capable Docker client:

```bash
docker build -f dashboard/Dockerfile -t knowledge-vault-dashboard:review .
sh scripts/ci/test_dashboard_image.sh knowledge-vault-dashboard:review
```

The Dockerfile-specific ignore file includes frontend sources only for that build. The core
Docker context excludes both `web/` and `dashboard/`; its image has no frontend build dependency.
The digest-pinned Node builder is discarded; the final non-root Python image holds the locked
BFF wheel, compiled public assets and frontend SBOM. It has no Node, core package, PostgreSQL
client, model or writer authority. It starts disabled without an explicit config. Chart enablement
and signed release publication do not establish browser proof through an operator's ingress or
Access path; record those results separately before accepting that deployment.

## Deployment preparation and private TLS

Use a disposable Minikube profile with a NetworkPolicy-enforcing CNI for rehearsal. Use only
synthetic assertions and freshly generated lab-only credentials; do not copy a production
database, Secrets, browser profile, tunnel token or kubeconfig into the fixture. Load the three
review images into that profile and retain their image IDs/digests. Check Docker data-root free
space first. A namespace on the live cluster is not equivalent to a disposable cluster.

For a live installation the operator must approve the target context/namespace, dashboard-only
hostname, private route, TLS Secret, image digest, exact-read inspection identity and browser
credential/session policy. The default is LAN/WireGuard HTTPS with no public route. Verify the
TLS certificate for the dashboard hostname in the browser; never make an insecure-browser
exception or disable verification to turn a failed proof into success. A lab may trust its own
ephemeral CA in an isolated browser profile. Keep the private hostname distinct from all MCP
hosts, including the public Access host. A dashboard hostname is not an alias for the core API.

Provision Secrets outside Helm from operator-local files with mode 0600 using the established
[token workflow](tokens.md). Use `scripts/generate_token.py --scopes knowledge:read` with
`--token-output` and `--record-output` so secret values never appear in terminal output. Generate
the service and browser tokens independently, with different principals and peppers. Register
only the service HMAC record in core, and list that principal in core's
`KNOWLEDGE_VAULT_INSPECTION_PRINCIPALS` configuration. Core token-record changes are an explicit
core rollout, completed and verified before a dashboard-only rollout. Never mount core token
records/pepper, a writer/admin token, database credentials, backup credentials or a model PVC
into the dashboard. Dashboard Secrets contain only its issued service bearer and, in private
mode, its own pepper, exact-read browser records and separate signing key. Missing objects/keys
must prevent startup. Wrong token authority or incompatible core capabilities must fail readiness;
do not replace those errors with a privileged credential.

The chart's enabled-only values and Secret key names are documented in
[`values.yaml`](../../charts/knowledge-vault/values.yaml). Set the dashboard image to a verified
published digest; verify the signature and release SBOM using [RELEASING](../../RELEASING.md).
The [private profile](../../charts/knowledge-vault/values-dashboard-private.yaml) is a starting
point for review, not ready-to-apply operator configuration. Required references are
`dashboard.serviceTokenSecret.name/key` (default key `service-token`),
`dashboard.privateSession.existingSecret` (`pepperKey: pepper`, `recordsKey: records`) and the
distinct `dashboard.privateSession.signingSecret` (`signingKey: signing-key`). Set
`dashboard.host`, `dashboard.coreHost` and `dashboard.image.digest`. Access mode instead requires
`dashboard.mode: cloudflareAccess` and `dashboard.access.issuerUrl`, `audience`, `allowedEmails`
and reviewed public `jwksEgressCidrs`; leave both private Secret references empty. An optional
Ingress requires `dashboard.ingress.enabled`, `className`, `tlsSecret` and explicit
`dashboard.networkPolicy.ingressFrom`. `dashboard.networkPolicy.allowCloudflared` requires
Access mode and the separately enabled existing tunnel; it grants only Pod connectivity.
`dashboard.secretRevision` is the explicit BFF-only rollout marker for externally changed Secrets.
Review the rendered public JSON ConfigMap: it must contain file references, never credentials.
Helm derives the fixed core URL from this release's API ClusterIP DNS name and service port.
The HTTP suffix is `.svc.cluster.local`; the separately configured core Host must be the
private authenticated core hostname. Do not send a public MCP Host from BFF to core. In Access
mode configure the dashboard's own issuer/audience/exact-email list and no private login keys.

Ingress must preserve the original single Host and `/dashboard/...` path unchanged. Route only
`/dashboard` with Prefix matching to the dashboard ClusterIP Service. Do not add a root route,
rewrite, external-auth bypass, core backend, health path, metrics path, NodePort, LoadBalancer
Service, hostPort or public bearer endpoint. Disable controller access logging of headers,
request bodies and query values. The chart does not configure a controller or its TLS policy:
review controller-specific middleware before applying. Only the selected ingress-controller
namespace AND Pod labels should be admitted by dashboard ingress NetworkPolicy. Namespace and
Pod selectors in separate peer entries mean OR, which is too broad. Opting in tunnel peers
requires a separate reviewed public-exposure decision. DNS egress is restricted to selected
DNS Pods, core egress to the named API Pods/port, and Access-mode HTTPS key fetching to configured
public destinations. L3 policies cannot restrict core HTTP paths or verify a JWKS hostname;
fixed BFF routing and origin JWT verification remain mandatory. Do not allow DB egress.

Before any authorized apply, render both off and on configurations, run Helm lint, strict
kubeconform and rendered-number checks, then run `kubectl --context <approved-context> apply
--dry-run=server -f <rendered-file>`. Keep rendered files private even though they contain Secret
references rather than values. Check namespace PodSecurity admission and that the CNI enforces
policies; a successful render or a policy object alone is no connectivity proof.

## Installation, upgrade, rollback and removal

Keep reviewed `core-values.yaml` separate from `dashboard-values.yaml`; neither contains secret
values. Record `helm get values`, `helm get manifest`, core image/schema revision and the core
API/worker/PostgreSQL/tunnel Pod UIDs and restart counts before each rehearsal. Do not print
Secrets or store raw request headers, browser HAR files, cookies, assertion bodies or SQL values
as evidence. Report content-free status, timings, counts, policy decisions and version/digest IDs.

1. Install and verify the compatible core release with ordinary migration hooks and dashboard
   disabled. An authenticated existing MCP client must read and write synthetic data. Provision
   the designated exact-read inspection identity separately; verify its named read API succeeds
   and ordinary MCP/read/write/admin routes refuse it. This is a precondition, not a dashboard
   side effect. Preserve the PR2/PR3 regression suites and independent finding dispositions.
2. Render off → on → off and compare every non-dashboard document, including Pod-template
   checksums, hooks, core policy, database, backup and worker. Repeat with only the dashboard image
   and only its Secret reference/revision changed. Any core difference blocks a dashboard-only
   operation. Check core image/schema/config against the installed compatible release as well.
3. For this verified dashboard-only change, use `helm upgrade <release> charts/knowledge-vault
   --namespace <namespace> -f core-values.yaml -f dashboard-values.yaml --no-hooks --wait
   --timeout 5m`. Initial core installation and any real core/schema upgrade retain hooks.
   `--no-hooks` is not a general migration workaround. Wait for dashboard readiness and perform
   the external authenticated browser test below; rollout status is only scheduling evidence.
4. Upgrade only the pinned dashboard image with the same comparison and `--no-hooks`. Preserve
   browser sessions only with compatible unchanged signing/credential configuration. Rotate or
   revoke dashboard material via BFF-only rollout; externally managed Secret contents are not
   hashed by Helm. Change the dashboard's Secret revision/Pod annotation or explicitly restart
   only the dashboard Deployment. Confirm core UIDs/restart counts remain unchanged.
5. For rollback, restore the previous dashboard image and public settings against the current
   compatible core. Prefer an explicit values-based `helm upgrade --no-hooks`; a full
   `helm rollback` can restore unrelated old core values. Use `helm rollback --no-hooks` only
   when full stored manifests prove core/schema/config identical. Verify private login and reads
   again, including readiness recovery. Never roll back database/core just to undo the UI.
6. Remove by applying the same compatible core values with `dashboard.enabled=false` through
   `helm upgrade --no-hooks`. Confirm every dashboard-owned Deployment, Service, ServiceAccount,
   ConfigMap, Ingress and additive policy disappeared, while all other resources, core Pod UIDs,
   restart counts and authenticated existing-client reads/writes remain unchanged. Do not
   uninstall the entire release or delete its namespace/PVC. Existing Secrets, inspection
   records, TLS/DNS and external routes are intentionally retained; operator cleanup of those
   needs separate authorization and must not revoke shared core client material.

Database/backup format does not change: dashboard has no persistent volume, tables or new restore
step. Run the existing read-only-root backup/restore and retention rehearsal against disposable
data, then repeat dashboard authenticated reads against the restored compatible core. Retain
external auth/TLS configuration in the operator's secret store; database backups do not restore it.

## Browser, routing, isolation and recovery acceptance

Use an isolated browser profile and synthetic corpus. Test through the deployed private TLS
ingress, from outside the dashboard Pod. A Pod-local request, port-forwarded BFF request,
readiness check, simulated Access edge or browser fixture is not this proof. Record the exact
commit, three image digests, chart values hash, Kubernetes/CNI/controller versions, timestamp,
browser version, target context and whether TLS trust was private-lab or real deployment trust.

- Log in with the browser credential; traverse Explorer All, lexical and ranked modes, Portal,
  provenance, paged relationships/artifacts, temporal history, ambiguous/conflicting state and
  raw evidence beyond resolution limits. Confirm malicious synthetic text remains inert, no
  external preview occurs and browser network requests contain no service bearer. Exercise
  concurrent first ranked use and check core model memory/log counters: only the existing API
  model loads. No model download or BFF model process is allowed.
- Verify dashboard Host succeeds unchanged, wrong/repeated Host fails and wrong/sibling Origin
  fails; named API routes stay JSON. `/mcp`, core `/api/v1`, `/health/live`, `/health/ready`, unknown
  API and encoded/rewrite bypass attempts must not reach a public core/probe route through the
  dashboard ingress. Check the core hostname still routes to its existing backend. Do not
  configure a rewrite just to hide a path-preservation failure.
- Confirm direct write/admin attempts from browser fail. A service-token inspection call may
  succeed only on named core read operations. Test from an unauthorised Pod: dashboard and core
  access must be denied under the explicit peer policy. From a dashboard-labelled probe with the
  same policy selection, core port is reachable, DB port 5432 and arbitrary external HTTP(S)
  are denied; private mode has no internet exception. In Access mode only selected JWKS HTTPS
  ranges are allowed. Use bounded connection timeouts and a positive control to distinguish
  policy denial from a broken target. Policy enforcement requires a supporting CNI; kind's
  default networking is not sufficient. Remove lab-only probes after recording status.
- In the disposable fixture only, test absent Secret, missing key, empty/wrong bearer, duplicate
  browser/service credential, write/admin/mixed browser records, wrong core Host, incompatible
  protocol, unavailable core and bad Access issuer/audience. Configuration errors must prevent
  startup or readiness/browser reads; none may silently enable a fallback or privileged route.
  Restore the original references and verify readiness returns without core reconfiguration.
- Restart only BFF: liveness stays independent of core, bounded readiness recovers and existing
  core MCP reads/writes continue. Temporarily stop the fixture's core API: BFF reports safe
  degradation, no DB fallback, no unbounded retries; restore core and verify readiness/browser
  reads recover. Wait beyond the five-second readiness cache when observing transitions.
- After browser calls read safe API/BFF logs and restart counts. Check for unexpected errors,
  extra model loads, leaked payloads/credentials and restarts. Do not paste logs containing
  sensitive fields into a PR; preserve only content-free exception descriptions/status counts.
  Repeat authenticated core MCP read/write after image upgrade, rollback and dashboard removal.

## Real Cloudflare Access provider verification

This is an operator-approved manual acceptance procedure. It deliberately does not run against
the live provider in CI. Locally signed JWTs and simulated edge tests remain valuable regressions,
but cannot establish provider session expiry, redirect or reauthentication behavior.

Before starting, name the target dashboard-only Access application, exact hostname, authorized
identity, current application/global/policy session durations, tunnel destination and rollback
values in a private change record. Obtain explicit approval for each needed live action: creating
or changing that Access application/policy/session duration, DNS record, dashboard-only tunnel
route, TLS/ingress, or credentials. Existing MCP Managed OAuth is not the dashboard browser
application. If any needed action is unapproved, stop **at that action**, report the exact object
and old→new setting required, and continue only offline work. Never shorten a shared production
MCP application's session or revoke a shared identity to accelerate this test.

1. On an already authorized dashboard-only route, verify origin Access mode has the expected
   issuer/audience/exact email, no private session material/fallback, and a dedicated exact-read
   service bearer. Confirm no origin bypass and direct public bearer route exist. Confirm the
   dashboard Ingress/tunnel admits only `/dashboard`, preserves Host/path, keeps probes private
   and sends traffic to dashboard Service rather than core. Record configuration identifiers,
   not credential contents. Use a synthetic-only dashboard/core fixture for public rehearsal.
2. Start Chromium and Firefox with clean isolated profiles. Authenticate normally at top level;
   verify the expected identity and synthetic Explorer/Portal flow. Record the provider's
   effective expiry time without recording/copying its cookie/JWT. The BFF must verify the
   origin assertion, not merely receive a trusted header. An unapproved/expired assertion or
   direct bypass must fail; the browser's Access identity must not authenticate core inspection.
3. Allow the **real effective provider session** to expire. Do not replace this step with a fake
   local JWT, deleting only the app UI state, mocking fetch, changing the workstation clock or
   editing a cookie. A dedicated short-lived lab policy can be used only after explicit approval
   and with its original settings recorded for restoration. Account for IdP/global/application
   session interaction; establish that Access, rather than just the BFF, rejected the session.
4. While the expired page stays open, trigger an actual browser AJAX read with same-origin
   credentials, `X-Requested-With: XMLHttpRequest`, BFF custom header and CSRF. Record only the
   HTTP status, content type, redirect/no-redirect, safe destination origin and which layer
   refused. Check console/network status without saving HAR/headers/bodies. Exercise the real
   observed non-JSON 401/403 and redirect/network-denial behavior; the client must clear private
   DOM/state and never render returned login HTML. If a behavior cannot be made to occur safely
   on the approved provider configuration, record it as unverified instead of simulating proof.
5. Use the UI's top-level reauthentication action. Verify navigation returns only to a named
   dashboard document on the original HTTPS hostname, the provider authenticates the intended
   identity, a fresh read succeeds, stale private state never reappears and another wrong
   identity is refused. Local Sign out clears views but does not claim to revoke Access/IdP;
   verify that distinction in the real browser. Repeat expiry/reauth in both engines.
6. Read safe origin API/BFF logs and restart counts after the real client call. Confirm no
   bypass, credential/payload logging, write/accounting change or core disruption. Restore any
   approved temporary provider settings and separately approved lab route cleanup; verify
   existing MCP public and private clients still operate. Record operator signoff, timestamps,
   exact image/chart head, per-engine observed outcomes and all unverified cases.

Production acceptance remains pending if this procedure, private-ingress browser proof, enforced
network isolation, signed publication or restored-core browser proof has not actually run. A
review-ready source PR may describe those authorization limits; it must not label them passed.

## Disposable lifecycle harness

`scripts/ci/test_dashboard_lifecycle.py` accepts only context/cluster `kv-dashboard-pr4` and
refuses an existing fixture namespace. Start that independent Minikube profile with Calico,
preserve the operator's current context (`--keep-context`), build/load the three review images,
and obtain the loaded dashboard manifest digest from containerd. Run from the repository root:

```bash
python scripts/ci/test_dashboard_lifecycle.py --context kv-dashboard-pr4 \
  --dashboard-image knowledge-vault-dashboard@sha256:<loaded-manifest-digest> \
  --core-image knowledge-vault:pr4-review --backup-image knowledge-vault-backup:pr4-review
```

The harness creates lab-only Secrets in memory and sends them over stdin, never argv/output.
It installs a synthetic core/pgvector fixture, tests default-off/enabled/upgrade/rollback/recovery/
removal, compares all non-dashboard rendered documents and core Pod UIDs/restarts, calls the
actual BFF/core with separate identities, and checks enforced policy positive/negative controls.
All commands use explicit context/namespace and bounded deadlines. Failed state remains in that
disposable profile for content-free diagnosis. Remove the entire **named disposable profile**
after reviewing results; never use this cleanup procedure against the live cluster. The harness
also captures API/BFF logs after authenticated operations, refuses any generated raw credential
or the exact synthetic assertion text in logs, and reports only safe verdicts/restart counts.

For a fresh disposable profile with Minikube's nginx ingress addon, add `--verify-ingress
--browser-script /absolute/path/to/verification.mjs`. The harness generates a one-day fixture
certificate/Secret, enables only the chart's dashboard TLS ingress and admits the combined
ingress-nginx namespace/controller Pod selector. While BFF is enabled, it invokes Node with
JSON on stdin: `url`, `host`, `ingress_address`, `browser_credential`, `ca_certificate` (public
PEM), and `tls_spki_sha256` (base64 SHA-256 of the fixture public key's DER SPKI). The callback
must resolve only the fixture hostname to that ingress address, narrowly trust its lab certificate
or public key, and fail on browser/routing/authentication errors. Do not disable all certificate
verification. A nonzero callback fails the rehearsal; credentials and callback output are never
printed. This proves only the disposable ingress path when actually run. It cannot establish
real Access expiry, image publication, live deployment trust or browser operation after backup
restore; those acceptance results must be recorded separately.
