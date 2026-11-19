# Optional Web UI implementation plan

Status: accepted staged plan with implementation verification gates; revised for [PR #126](https://github.com/kimonus/knowledge-vault-mcp/pull/126).
Four future implementation PRs; no application implementation, deployment or merge in this phase.

Implementation tracking: PR1 and PR2 are merged. PR3 supplies Explorer/Portal and the independent
image; its [verification record](../../reviews/web-ui-pr3.md) tracks review readiness. PR4 remains
unimplemented. The phase/authorization descriptions below record the accepted design baseline;
they do not authorize deployment or replace later explicit operator instructions.
Read the [assessment](assessment.md), [ADR 0009](../../decisions/0009-optional-read-only-dashboard.md),
[API/UI contract](api-ui-contract.md), [security design](deployment-security.md) and
[review disposition](review-response.md). The maintainer accepted the revised ADR on 2026-11-19;
PR1 may start after the accepted design is merged under the operator's instruction.

## Sequence and ownership

Recommended chain: **PR1 → PR2 → PR3 → PR4**. Review/merge each independently with applicable
checks passing; no dependency on unmerged branches. Roles below may be one maintainer. No PR
publishes or enables a dashboard by default. Core read API can ship/usefully operate without
building or deploying dashboard; BFF/SPA use their own image and runtime.

### PR1 — Core read services and least-privilege HTTP interface

Owner: backend maintainer. Dependency: revised ADR accepted. Add shared query/input/response
models and bounded services for exhaustive browsing/strict lexical matching, catalogs, paged
sources/artifact refs/identities, direct links/reverse supersession/conflicts and raw evidence.
Factor a bounded detail projection and read-only session factory. Add core `/api/v1/read` handlers
with explicit operation allowlist, exact-read bearer/inspection-principal restriction, forced
maintenance purpose and shared rate guard. Reject inspection identities outside this interface
in both HTTP and MCP auth; other clients' existing contracts/behavior remain unchanged. Core
inspection config is independent of `dashboard.enabled`, not browser auth or a feature toggle.

Acceptance:

- A static synthetic corpus larger than every ranked candidate cap is visited exactly once to
  exhaustion in all/lexical mode, across all four statuses and unclassified/Unicode data. Cursor
  tampering, expiry, wrong principal/filter/query/alias version fail; deleted boundaries work.
  Concurrent mutation tests document live-scan misses/membership shifts, not snapshot completeness.
- Catalog counts match distinct eligible assertion IDs across the full fixture, canonical aliases,
  overlapping topics, missing classifications and evidence/workflow filters. No hidden top-N cap.
- Detail child pages traverse beyond 32 sources/identities and 100 relations/artifacts; bound DB
  loading as well as output, and demonstrate no list-size-dependent N+1 query behavior.
- Raw evidence remains pageable beyond 1,000 records; resolution still refuses overflow without
  choosing a partial winner. No full edit-history/deleted-content recovery claim.
- Exact-read inspection token succeeds only on named `/api/v1/read` routes. Write/admin/mixed-scope
  tokens and Access identities fail there. Inspection token fails on all ordinary read, usage,
  write/admin and MCP routes, including encoded/alternate paths. Normal clients remain compatible.
- With usage tracking enabled, every inspection operation leaves knowledge/usage/maintenance data
  unchanged; attempted SQL write in its read transaction fails. Pooled normal writers still work.
- Ranked reads use the existing API embedder object; no new Container/model. Browse/catalog calls
  need no embeddings. Shared responses/OpenAPI have reviewed fixtures and future BFF compatibility
  contracts. No browser assets/dependencies/routes/session settings enter core build/runtime.
- Record SQL plans/timings on documented hardware. Initial proposed gate: 50-item browse/catalog
  p95 under 500 ms over 50,000 synthetic assertions, excluding embeddings; revise only with evidence.
  Any justified index needs a new explicit Alembic revision, never edits to an existing revision.

Rollback: additive core API/service release can be reverted with ordinary core release procedure;
no knowledge rewrite. Once a BFF depends on this version, disable/rollback BFF first. Disabling a
dashboard does not roll back this core release, remove service records or restart core Pods.

### PR2 — Separate Python BFF and browser security boundary

Owner: security/backend maintainer. Dependency: PR1. Create dashboard-only Python package/lock,
entrypoint and tests for a standalone FastAPI app. Use fixed httpx operation mapping to core read
API, no generic proxy/core Container/SQL/model dependencies. Add separate browser HMAC records/
pepper, exact-read login, signed sessions, Access alternative, Host/Origin/CSRF/CSP/no-store rules,
local browser rate guard and bounded upstream client. No core browser routes. Package test assets
for boundary tests without publishing a partial SPA or enabling Helm resources yet.

Acceptance:

- BFF starts without a core DB URL, core token pepper, core bootstrap records, model volume or
  weights; none can be required/mounted. Its issued core service token is exact-read only. Reject
  missing upstream credential/config; never fall back to browser or privileged credentials.
- Browser token and BFF service token are distinct. Browser login token/session cannot authenticate
  at core; BFF does not forward browser Authorization/Cookie/Access/Origin/client IDs. Outbound
  requests use fixed ClusterIP URL/private core Host and explicit path/method; redirects, caller
  upstream/headers, oversized responses, timeout/retry storms and arbitrary proxy paths fail.
- Browser/private and Access authentication reject wrong/repeated Host/Origin, spoofed forwarding,
  wrong audience/expiry/signature, admin/write/mixed scopes, cross-site/sibling-site CSRF and
  encoded-path bypasses. Access mode has no private fallback. App API returns JSON auth errors.
- Cookie has Secure/HttpOnly/Strict/no Domain, 30-minute absolute expiry; login record removal or
  signing-key rotation revokes after BFF-only rollout. Logout expires local cookie but explicitly
  does not revoke a copied cookie before expiry. No session DB/browser credential storage.
- BFF credential compromise cannot gain write authority or direct DB access. Tests exercise core
  inspection route confinement with the actual upstream token; browser auth alone is insufficient.
- Upstream 401/403 (service misconfiguration) becomes safe 503, not a user-login loop; 429 preserves
  safe Retry-After; timeouts/dependency failure show bounded 503/504 and no DB fallback.
- Core unavailable/restarted: BFF reports degradation; BFF absent/crashed: core readiness/clients
  continue normally. Version/capability check refuses incompatible read protocol without silently
  calling ordinary routes. No second model load is possible from dashboard imports/runtime.

Rollback: stop/remove only BFF; prior core read API and clients keep operating. Update security/
threat-model proposals alongside auth design; no change to stored knowledge.

### PR3 — Explorer/Portal and independent dashboard image

Owner: frontend maintainer. Dependency: PR2. Add locked React/TypeScript/Vite UI, OpenAPI-derived
fetch client, routes/components from contract. Implement login; all/lexical/ranked Explorer;
complete detail/provenance/relations/history; context/topic/facet Portal; as-of/known-at and raw
evidence fallback. Build a separate dashboard image using pinned Node builder and minimal Python
runtime. Core/API/worker Dockerfile, build graph and runtime remain independent of web/BFF assets.

Acceptance:

- Browser reaches every retained fixture assertion/child page; Portal matches equivalent filters;
  all-status and unclassified buckets remain accessible. Ranked exhaustion is labelled bounded.
- Fixtures distinguish current/uncertain/disputed/superseded, proposed workflow/temporal proposal,
  considered/rejected kinds, verified evidence and ingestion confirmations. No UI/rank/recency
  promotes an option/proposal to truth. Precision, unknown/ambiguous/conflict and overflow visible.
- Malicious HTML/JS/Markdown/source locators/artifacts render inertly; safe links only, no previews
  or external network fetches. Browser XSS/CSP/security tests assert actual behavior.
- Component and Chromium/Firefox Playwright flows exercise filters/page/reset/back, stale requests,
  empty/degraded/401/429 states, temporal continuation, session expiry/logout, mobile/keyboard/focus/
  labels. Tests verify behavior rather than merely mirroring markup.
- Access-mode fetches send same-origin credentials and `X-Requested-With: XMLHttpRequest` as
  well as the BFF headers. Treat non-JSON edge 401/403, login redirects and network failures as
  authentication failures without rendering response HTML; origin JSON guarantees end at BFF.
- `npm ci`, frontend lint/format, `tsc --noEmit`, build/tests/Playwright pass. Shared core read/BFF
  OpenAPI/generated TypeScript have no drift; fake upstream contracts remain backward compatible.
- Dashboard artifact has no Node runtime/model/DB engine requirement/core Container or secret,
  content, public source map/service worker. Core builds/installs work without Node, web directory,
  BFF dependencies or frontend build artifacts. Reuse pure auth helper source without installing
  the full core runtime in BFF or maintaining a separate copied auth implementation.

Rollback: dashboard image version rollback only, subject to core read protocol compatibility.

### PR4 — Conditional Helm resources, release integration and operational proof

Owner: deployment maintainer. Dependency: PR3. Add `dashboard.enabled: false`, separate image
values/schema, conditional Dashboard Deployment/ClusterIP Service/ServiceAccount/ConfigMap/
NetworkPolicies, optional TLS Ingress and Secret refs. Add conditional additive core-ingress
NetworkPolicy only if needed, leaving original core policies/templates unchanged. Dashboard-only
settings/checksums never enter shared core ConfigMaps or Pod templates. Add dashboard release
artifact/SBOM/signature/audit/CI workflow and runbooks. No Compose profile: reassess actual merged
standalone support first; none is tracked at the design baseline.

Acceptance:

- Disabled render produces **zero dashboard resources**, requires no dashboard credentials/image/
  hostname and leaves core resources/checksums unchanged. Enabled without ingress creates only
  private ClusterIP dashboard resources. Ingress points at dashboard Service, never core.
- Compare full non-dashboard manifests (including Pod-template checksums, hooks, Services, policy,
  DB, worker/backup) for false/true/false, BFF version update and BFF Secret changes; no differences
  attributable to dashboard. Explicitly test chart's shared checksum helper to prevent coupling.
- A disposable Minikube upgrade true→false removes exactly dashboard-owned resources, including
  optional ingress/additive policy, while all core clients/Pods keep functioning without restart.
  External Secrets, DNS/TLS and core inspection token records are not deleted by the toggle.
  The existing migration hook runs on ordinary full-chart upgrades: for dashboard-only changes,
  compare unchanged core image/schema/config first, then rehearse `helm upgrade --no-hooks` so
  UI removal does not rerun core migrations. Do not skip hooks for a real core/schema upgrade.
- DB NetworkPolicy never admits dashboard; BFF egress only DNS/core and opted-in Access JWKS,
  ingress restricted to controller/opted-in tunnel peers. No NodePort, public route, hostPort,
  privileged token/DB/core-auth mounts. Validate least-privilege resource contexts/probes/limits.
- Helm lint/template, strict kubeconform/rendered-number checks pass disabled/private/Access,
  default/reference values and existing Cloudflare enabled/disabled profiles. Invalid host overlaps,
  missing enabled-only values/TLS/keys/upstream and downgrade configurations fail closed.
- Build/scan existing app+backup and new dashboard images; required backup/restore retention
  rehearsal still passes for chart changes. Check shared Docker storage first; clean only new
  review images. Locked SBOM/security gates inventory bundled frontend/BFF independently.
- Full Python/real pgvector/MCP/Inspector and new BFF/browser gates pass, maintaining at least
  90% statements/85% branches without exclusions. Future authorized Minikube apply uses server-side
  dry-run first. A real browser verifies private TLS/auth, all/Portal/provenance/relations/state/
  history, concurrent cold ranked use (only existing API model), failed direct writes and dashboard
  removal, while authorized existing MCP still reads/writes. Read safe API/BFF logs and restart
  counts afterward. Readiness/rollout/in-Pod calls alone are not proof; report missing proof.
- In the opted-in Access profile, expire the real provider browser session and verify edge
  rejection, non-JSON/redirect handling and top-level reauthentication. A fake origin JWT is
  insufficient evidence of Access edge expiry behavior. Keep health probe paths internal.

Sync README/CHANGELOG, SECURITY/threat model, minikube/Cloudflare/client-setup/monitoring/embedding
runbooks, RELEASING and new dashboard runbook. Add independent review/finding status under
`docs/reviews/`. Existing MCP docs/flush skill change only if their public contracts actually
change; none are planned. Keep one chart as deployment definition.

Rollback: disable dashboard and remove its resources only, or roll back only its image. Do not
roll back core/worker/database to remove UI. If actual public routing was explicitly provisioned,
operator removes that dashboard-only route separately. No destructive migration/data rewrite.

## Verification and decisions

Run narrow tests then applicable full gates from AGENTS.md: uv lock, Ruff format/lint, Pyright,
unit/property/contract and real disposable pgvector integration/e2e/coverage, Helm/kubeconform/
rendered-number, supply-chain and Inspector. Container/chart work adds existing two-image and
dashboard-image builds and backup/restore retention. Tests never download models/read local `.env`;
use explicit settings/synthetic knowledge/fake embeddings. Property tests cover deterministic
grouping/keysets; integration covers core read route confinement/SQL/pool reuse; browser tests
cover actual security and user flows. No new persistent DB, LLM or coordination service.

This revision runs **documentation validation only**: local links, Markdown fences/JSON examples,
whitespace/diff, proposal consistency and review-finding traceability. No implementation, browser,
performance, Helm render, image or deployment gate is claimed as executed for this design.

ADR acceptance is recorded in its decision section. Before enabling, operator supplies private
hostname/TLS, dashboard-only login/pepper/session keys, exact-read core inspection identity and
compatible API version, and accepts live traversal, all-sensitivity access and coarse session
revocation. Access/public DNS/tunnel changes require separate approval. Current authorization is
to merge the accepted design after checks and implement/open PR1 for review; no deployment,
infrastructure changes or PR1 merge. All review findings are tracked in [review-response.md](review-response.md).
