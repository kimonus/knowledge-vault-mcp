# ADR 0009: Optional, private, read-only Knowledge Vault dashboard

- Status: Proposed; implementation and deployment are not authorized by this ADR
- Date: 2026-11-19
- Assessed baseline: `39b6af7c27950a7e57159b59cc7157ff8728f248` in
  `kimonus/knowledge-vault-mcp` (local `main`)
- Scope: design only; supersedes the original brief's exclusion of a web UI only if accepted

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
route library, and ordinary CSS. Build static assets once; serve them from the existing FastAPI
process under `/dashboard/`, before its final MCP catch-all mount. Node is a build/development
dependency only. No SSR, Next.js, Node runtime, graph database, LLM, Redis, or new persistent store.
Pin exact frontend dependencies and commit the npm lockfile during implementation. Python-only
development and the disabled core must continue to work without Node or a frontend build.

Use a dedicated dashboard hostname, distinct from both MCP hostnames, even though the origin
Service and process are shared. A server-side host/path boundary admits only `/dashboard/` on
that host, refuses core MCP/HTTP/admin paths there, and refuses dashboard paths on other hosts.
This boundary applies even to a valid write/admin bearer or an Access identity. Normalize and
validate a single Host using existing helpers; do not trust arbitrary forwarding headers.
Retain probe access inside the cluster. Unknown dashboard API paths never fall back to the SPA.

Add a narrow dashboard HTTP adapter over shared read services. All authenticated dashboard
principals have exactly `knowledge:read`. Only explicitly listed query operations exist. Reuse
domain requests/responses, validation, the shared rate guard, artifact reading, and the temporal
resolver. Force search/fetch `purpose="maintenance"` in server code, with no caller override or
usage report. Dashboard service sessions must also execute in PostgreSQL read-only transactions;
reuse service logic with a read-only session factory and the **same** embedder object. Do not
build another full Container or load another model. This is defense in depth against accidental
usage/maintenance writes, not a new database or a replacement for scope enforcement.

Expose exhaustive keyset browsing and metadata catalogs as new shared service methods. Keep
hybrid search explicitly ranked and bounded. Do not change its existing MCP semantics. Add
paginated adjacency, provenance, artifact references and identity discovery where existing detail
responses truncate or cannot be resumed. No migration or data rewrite is required by the design;
an index justified by measured query plans would need its own new Alembic revision.

### Browser authentication

Default deployment is private LAN/WireGuard HTTPS, with no public dashboard route. A login form
exchanges a **dedicated read-only** device credential for a short-lived, signed HttpOnly cookie.
Reuse HMAC credential verification, but require the matched credential's exact scope set to be
`{knowledge:read}`: reject write/admin credentials rather than downscoping an accidentally pasted
privileged credential. The browser never receives a privileged bearer. The read-only login
credential is sensitive too: never persist it in JavaScript storage, URLs, logs, or a bundle.

The cookie contains only principal, audience/host, issue/expiry times, session nonce and an opaque
keyed reference to the still-enabled credential record. Sign with a separate Secret key and an
explicit allowed algorithm; no token value or stored token digest goes into the payload. Check
the referenced active read-only record on **every** request. Removing a credential revokes its
sessions after configuration rollout. Absolute lifetime is 30 minutes, with no automatic renewal;
key rotation invalidates all sessions. Local logout expires the cookie but does not revoke a
copied cookie before expiry; immediate revocation requires credential removal or key rotation.
No session database is introduced. [Deployment/security details](../design/web-ui/deployment-security.md)
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
| Separate frontend/BFF Deployment | Adds networking, credential forwarding and another runtime; unnecessary for this single-user service |
| Reuse ordinary `/api/v1` directly from browser | Insufficient isolation: Access has write scope and ordinary reads can write usage metadata |
| Store a privileged API token in SPA/localStorage | Rejected: exposes write/admin authority to browser code and stored-content attacks |
| Add topic pages as stored Markdown or LLM summaries | Rejected: new durable state/invalidation or invented synthesis; deterministic projections suffice |
| Compose profile now | Unsupported by this checkout; do not introduce a parallel deployment stack for this UI |

Benefits: one origin/runtime, no duplicated model, bounded reads, unchanged core services and
transport contracts, and a UI that can be removed by disabling a flag. Costs: a small frontend
toolchain, new browser threat surface, credential/session lifecycle, host-aware routing, query
costs for catalogs, and shared API CPU/memory. Browser readability cannot restore missing history
or repair incomplete metadata. No performance claim is made without implementation measurements.

## Rollout, rollback and approval

Helm declares `dashboard.enabled: false`; enabling it alone creates no ingress or tunnel route.
Disabled startup registers no dashboard routes or auth dependencies. The bundle may be present
but is unreachable. Core API, MCP, workers, migrations, backup and probes keep operating. Rollback
disables the flag and removes explicitly enabled dashboard routing; no stored knowledge changes.

Accept this ADR before implementation. Operator choices before enabling are the private hostname,
TLS Secret, dedicated read-only credential, separate session-signing Secret, session lifetime,
and all-sensitivity visibility. Selecting Access/public routing is a separate explicit exposure
decision. No operator response is needed to complete this design phase.

Implement in four reviewable PRs, in order: read services/contracts; browser security boundary;
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
