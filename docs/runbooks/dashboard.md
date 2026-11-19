# Optional Knowledge Dashboard

The separate `dashboard/` Python package implements ADR 0009's browser security boundary.
It is disabled by default. PR3 adds the React/TypeScript Explorer and Portal and a separate
dashboard image. There is no Helm workload or ingress. Do not deploy or provision credentials
from this runbook yet: PR4 supplies conditional deployment resources and
operator verification. Existing core HTTP/MCP clients and readiness do not depend on this process.

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

## Configuration and future Secret references

The entrypoint is `knowledge-vault-dashboard [--config /path/to/public-config.json]` on port 8080.
Without a config it serves only disabled probes. It never reads `.env` or core environment settings.
The JSON config has `enabled` (default false), `host`, `core_url`, `core_host`, `mode`
(`privateSession` by default), and `service_token_file`. These names describe local file references
for future read-only Kubernetes Secret mounts; PR2 creates no Secret or chart resource.
Direct `service_token`, `pepper`, `signing_key` and `records` values in that config are rejected.

Private mode also requires `pepper_file`, `signing_key_file`, and `records_file`. The first two
hold different random keys of at least 32 characters. Record JSON uses the existing token record
shape (`principal_id`, HMAC-SHA256 `sha256`, `scopes`), with **only** `knowledge:read` on every
record. Browser credentials must be different from the service bearer and must never be registered
at core. Generate records through the existing offline token workflow with an explicit read-only
scope; its usual write-capable defaults are unsuitable. Never mount the core token pepper/records,
DB Secret, writer/admin bearer or model volume into the dashboard.

The hostname must differ from the private core Host and, during future ingress review, both MCP
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
PR4 must test an actually expired Access browser session through the configured edge, including
its response format and reauthentication flow; origin tests cannot prove that behavior.
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
PR4 deployment/client proof remains required before enabling. See the
[PR3 verification record](../reviews/web-ui-pr3.md) for coverage and remaining review requirements.

## Explorer and Portal

The Explorer defaults to **All retained**, including every status. Exact text browsing also
uses exhaustive keyset pages; ranked search is explicitly a bounded candidate set. Filters cover
topics, context IDs, kinds, status, sensitivity, evidence, workflow, origins, temporal role,
confidence and available dates. Unclassified topic/context/facet filters apply to exhaustive
browsing, not ranked search. Reset and applying new filters reset the cursor. Previous pages
retain at most 100 cursor boundaries in session memory. A live scan is not a snapshot: concurrent
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
client, model or writer authority. It starts disabled without an explicit config. PR4 must add
conditional Helm resources, resource/security/network boundaries, signed release artifacts and
authorized browser proof through the chosen ingress/Access path. Nothing here enables them.
