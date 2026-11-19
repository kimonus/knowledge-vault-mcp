# ADR 0009: Optional, private, read-only Knowledge Vault dashboard

- Status: Proposed; implementation and deployment are not authorized by this ADR
- Date: 2026-11-19
- Assessed baseline: `39b6af7c27950a7e57159b59cc7157ff8728f248` in
  `kimonus/knowledge-vault-mcp` (local `main`)
- Scope: design only; supersedes the original brief's exclusion of a web UI only if accepted
- Revision: addresses the [PR #126 architecture review](https://github.com/kimonus/knowledge-vault-mcp/pull/126#issuecomment-7003860752)
  of `59c789b`; ADR remains Proposed and requires re-review before PR1

## Context and evidence

The Explorer must enumerate retained assertions without relying on ranked retrieval. The Portal
must make existing knowledge readable without inventing a taxonomy, inferred relationships, or
truth. Both must preserve the Vault's services, PostgreSQL source of truth, and optional deployment.

The [repository assessment](../design/web-ui/assessment.md) records verified capabilities and
gaps. No running deployment, private knowledge, unmerged worktree implementation, or remote branch
was used as evidence of supported functionality. Proposed paths and settings below do not exist
in the assessed baseline.

## Decision proposal

Build a client-rendered React/TypeScript application with Vite, native browser fetch, a small
route library, and ordinary CSS. Build static assets once and serve them under `/dashboard/`
from a **separate Python FastAPI browser backend (BFF)**. One dashboard image packages the static
SPA and BFF; Node is build/development-only. A separate Dashboard Deployment and ClusterIP Service
exist only when `dashboard.enabled=true`. No SSR, Next.js, Node runtime, graph database, LLM,
Redis, new persistent store or model process is introduced. Pin frontend dependencies and commit
the npm lockfile during implementation. Core API/worker images, runtime dependencies, entrypoints
and builds do not include or require dashboard assets or frontend tools.

Use a dedicated dashboard hostname, distinct from both MCP hostnames, routed only to its own
Service. The BFF admits only named dashboard routes and internal probes. It has no MCP mount,
writer/admin router, database engine, model loader or generic proxy. Normalize and validate a
single Host using existing helpers; reject unexpected hosts and origins, regardless of bearer or
Access credentials. Do not trust arbitrary forwarding headers. Unknown API paths never fall back
to the SPA. Core API never registers browser/dashboard routes or accepts browser session cookies.

The BFF invokes an authenticated, transport-independent **core read interface** exposed over
HTTP at `/api/v1/read` in the existing API process. This additive API is usable by exact-read
headless clients independently of dashboard deployment. It wraps shared domain/services,
validation, the rate guard, artifact reading and temporal resolver. A fixed method/path mapping
forwards only bounded read requests; no caller-selected upstream, forwarded auth/cookies or
arbitrary core route. Its own exact-read service bearer remains server-side in a dedicated Secret.
The BFF receives **no core token pepper, privileged token records or PostgreSQL credential**.

Core read handlers force search/fetch `purpose="maintenance"`, with no caller override or usage
report, and use transaction-read-only sessions for defense against accidental accounting writes.
The BFF has no DB role at all: database authorization stays in the core. Sharing an unrestricted
DB credential or relying only on read-only transactions in a DB-connected dashboard is rejected.
The core read interface shares the **existing API embedder object**, never constructing a second
Container/model. Browser ranked search traverses this interface; browse/Portal needs no model.
The existing embedding worker remains unchanged. All dashboard and service principals have
exactly `knowledge:read`; the read interface rejects write/admin scope sets rather than relying
on `Principal.permits(READ)`, which also permits admin.

Designated inspection service principals are configured independently of the Helm dashboard
toggle and restricted by core auth to `/api/v1/read` only, including refusing ordinary read routes
that can record usage and the MCP adapter. No new scope or database relation is needed. Provision
their exact-read records through the existing core token workflow; never mount that core Secret
or pepper into the dashboard. The BFF uses only the issued read bearer in a separate Secret.

Expose exhaustive keyset browsing and metadata catalogs as new shared service methods. Keep
hybrid search explicitly ranked and bounded. Do not change its existing MCP semantics. Add
paginated adjacency, provenance, artifact references and identity discovery where existing detail
responses truncate or cannot be resumed. No migration or data rewrite is required by the design;
an index justified by measured query plans would need its own new Alembic revision.

### Browser authentication

Default deployment is private LAN/WireGuard HTTPS, with no public dashboard route. A login form
exchanges a **dedicated read-only** device credential for a short-lived, signed HttpOnly cookie.
Reuse HMAC credential verification with a **dashboard-only pepper and credential records**, but
require the matched credential's exact scope set to be
`{knowledge:read}`: reject write/admin credentials rather than downscoping an accidentally pasted
privileged credential. The browser never receives a privileged bearer. The read-only login
credential is sensitive too: never persist it in JavaScript storage, URLs, logs, or a bundle.
Browser-login credentials are not core device credentials and do not authenticate at core HTTP/MCP.
They are distinct from the BFF-to-core service credential; neither is a write/admin credential.

The cookie contains only principal, audience/host, issue/expiry times, session nonce and an opaque
keyed reference to the still-enabled credential record. Sign with a separate Secret key and an
explicit allowed algorithm; no token value or stored token digest goes into the payload. Check
the referenced active read-only record on **every** request. Removing a credential revokes its
sessions after configuration rollout. Absolute lifetime is 30 minutes, with no automatic renewal;
key rotation invalidates all sessions. Local logout expires the cookie but does not revoke a
copied cookie before expiry; immediate revocation requires credential removal or key rotation.
No session database is introduced. All browser session keys/records live only in dashboard Secret
references; their rollout/revocation does not restart the core. [Deployment/security details](../design/web-ui/deployment-security.md)
specify CSRF, cookie and ingress behavior.

An operator may instead select Cloudflare Access browser authentication. Reuse origin JWT
signature/issuer/audience/expiry/exact-email validation, clamp the resulting dashboard principal
to read-only, and require it on every request. Do not enable the private credential-exchange
route in this mode or accept a bearer fallback. Reuse the Access browser session; no app cookie
or OAuth callback listener is needed. Public tunnel/DNS/Access changes require explicit operator
approval and a separate route review. Do not assume Managed OAuth for hosted MCP is already a
configured browser application for the new hostname.

### Information model

Explorer opens in **All retained assertions**, with all four statuses included, immutable-ID
keyset pagination and explicit filters. Ranked search is a separate mode. The detail view shows
content, metadata, provenance, artifacts, explicit relationships, supersession and structured
temporal evidence. Portal provides contexts, canonical topics, facets and direct typed links;
all pages remain projections over existing records, with links back to Explorer.

Keep status, kind, evidence state, workflow state, confidence and temporal mode as separate
dimensions. `current` means active evidence, not established truth. `proposed` is a plan workflow
state or temporal proposal mode; considered/rejected are kinds; verification is evidence metadata;
confirmation counts record repeated ingestion, not independent verification. Only the existing
resolver establishes `resolved`, `ambiguous`, `conflict` or `unknown` for an explicit identity.
No UI code selects a winner from timestamps or rank. Missing temporal classification is visible.

## Alternatives and consequences

| Alternative | Assessment |
|---|---|
| Render everything with server templates | Less JS tooling, but more bespoke client state for two filterable views; viable fallback if frontend maintenance is rejected |
| Core-process dashboard routes / route-registration flag | Rejected by PR #126: optional feature does not meet independently deployable component semantics |
| Separate Python SPA+BFF Deployment calling core read API | Selected: independent resources/image, no DB access, no second model; one bounded server hop |
| Dashboard-only Python process with direct PostgreSQL access | Not selected: requires SELECT-only DB grants/role and more persistence coupling; sharing the core writer role is forbidden |
| Reuse ordinary `/api/v1` directly from browser | Insufficient isolation: Access has write scope and ordinary reads can write usage metadata |
| Store a privileged API token in SPA/localStorage | Rejected: exposes write/admin authority to browser code and stored-content attacks |
| Add topic pages as stored Markdown or LLM summaries | Rejected: new durable state/invalidation or invented synthesis; deterministic projections suffice |
| Compose profile now | Unsupported by this checkout; do not introduce a parallel deployment stack for this UI |

Benefits: one browser origin, independent dashboard rollout/removal, no duplicated model, bounded
reads, additive core read capabilities and unchanged existing transport contracts. Costs: a small
frontend toolchain, one Python BFF runtime/network hop, new browser threat surface, separate
credential/session lifecycle and query costs on the existing core. Browser readability cannot restore missing history
or repair incomplete metadata. No performance claim is made without implementation measurements.

## Rollout, rollback and approval

Helm declares `dashboard.enabled: false`. False renders **zero dashboard resources**. True creates
its Deployment, ClusterIP Service, dedicated ServiceAccount, ConfigMap and NetworkPolicies; a TLS
Ingress is a separate opt-in. Existing Secrets are operator-managed, never created/deleted by the
toggle. No ingress, tunnel route, DNS or public exposure follows merely from enabling dashboard.
Disabling removes only dashboard-owned resources. No dashboard values/Secret checksums enter
core/worker Pod templates; enabling, upgrading or disabling dashboard must not restart, reconfigure
or remove existing API, MCP, worker, database, migration or backup resources. Supporting core read
API ships as an ordinary prior core release; its availability does not depend on this flag.
Core readiness never depends on the BFF. BFF failure affects only dashboard availability.
The existing full-chart migration hook runs on every ordinary Helm upgrade. Dashboard-only
upgrades use the render-verified `--no-hooks` procedure on a compatible unchanged core release;
actual core/schema upgrades retain normal migration hooks. See the deployment lifecycle design.

Accept this ADR before implementation. Operator choices before enabling are the private hostname,
TLS Secret, dashboard-only read credential/pepper, separate session-signing Secret, exact-read
core service credential, compatible core read API version, session lifetime,
and all-sensitivity visibility. Selecting Access/public routing is a separate explicit exposure
decision. No operator response is needed to complete this design phase.

Implement in four reviewable PRs, in order: core read services/interface; separate BFF/browser security;
Explorer/Portal assets; Helm/package/operational verification. See the
[implementation plan](../design/web-ui/implementation-plan.md) and
[API/UI proposal](../design/web-ui/api-ui-contract.md).

## External guidance checked

These inform proposals, not repository facts. No external dependency version was selected here.

- [React: building an app from scratch](https://react.dev/learn/build-a-react-app-from-scratch)
  documents a build-tool-based SPA option including Vite/TypeScript.
- [Cloudflare: validating Access JWTs](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)
  describes the browser cookie and origin assertion; origin validation remains required.
- [OWASP: session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
  informs HttpOnly/Secure cookies, bounded sessions and revocation considerations.
