# Optional Web UI implementation plan

Status: proposed; four future implementation PRs, no application implementation in this phase.
Baseline and verified capabilities: [assessment](assessment.md). Decision:
[ADR 0009](../../decisions/0009-optional-read-only-dashboard.md). Contracts:
[API/UI](api-ui-contract.md). Operational design: [deployment/security](deployment-security.md).

## Delivery sequence and review boundaries

Accept ADR 0009 first. Recommended dependency chain is **PR1 → PR2 → PR3 → PR4**. Each PR must
be independently reviewable and pass applicable gates; no PR depends on unmerged code. Keep
the application flag false throughout. Intermediate PRs do not publish a partially secured UI.
Implementation owners are the backend, security/browser, frontend and deployment maintainers
respectively; in this single-maintainer project these roles may be the same person.

### PR1 — Bounded read services and shared contracts

Dependencies: accepted ADR. Add service/query models and shared response DTOs for exhaustive
assertion browsing/strict lexical matching, catalogs, paged source/artifact/identity collections,
direct relations including reverse supersession/conflicts, and raw evidence history. Factor bounded
detail projection and a transaction-read-only session seam; retain existing MCP and HTTP behavior.
Add reviewed contract fixtures/OpenAPI definitions in tests; production dashboard routes remain
unregistered until PR2. No schema migration unless query-plan evidence justifies a new index.

Acceptance:

- A static corpus larger than every existing ranked candidate cap is visited exactly once to
  cursor exhaustion in all/lexical modes, with all statuses, missing classifications and Unicode.
  Invalid/expired/cross-filter/cross-principal cursors fail; deleting the boundary row still works.
- Catalog counts match distinct eligible IDs over the whole fixture, including aliases, overlap,
  null buckets, contexts/facets and evidence/workflow filters. Input normalization/order and
  alias-change invalidation are deterministic. No hidden “top N” replaces exhaustive catalogs.
- Child pages traverse beyond 32 sources/identities, 100 relations and preview artifact limits;
  query loading and response sizes remain bounded. No ORM eager-loading of an entire high-degree
  collection simply to slice it afterward. No N+1 query growth with list page size.
- Raw evidence inspection works beyond 1,000 records while existing resolution still refuses
  overflow; no partial winner, edit-history reconstruction or deleted-content resurrection.
- With usage tracking enabled, dashboard-style reads make no knowledge/usage/maintenance writes.
  An attempted write in a read-only session fails; pooled writer sessions still write normally.
  Existing search ranking/default scopes/MCP schemas and non-dashboard usage accounting pass.
- SQL plans/timings for representative synthetic small/large corpora are recorded; proposed
  initial budget is p95 under 500 ms for a 50-item browse/catalog query over 50,000 synthetic
  assertions on documented hardware. This is a proposed gate, not a measured guarantee; narrow
  or revise the target with evidence. No embedding timing is folded into that SQL budget.

Rollback: revert new query methods/DTOs; no data change. Any measured index addition is additive
and has its own migration/rollback discussion; never edit an old migration.

### PR2 — Feature gate, hostname isolation and browser authentication

Dependencies: PR1. Add disabled config, host/path dispatcher and dashboard router. Register the
explicit read operations, private credential exchange/session lifecycle and optional Access
profile, with Origin/CSRF/CSP/no-store protections. Reuse shared services/rate guard/embedder.
Security handlers are usable with test clients before the SPA exists. Reject enabled startup
without packaged test/production assets rather than providing a public scaffold page.

Acceptance:

- Dashboard disabled: routes unavailable, no keys/hosts/assets required, existing MCP/HTTP
  initialization, auth, readiness and ingestion fully operational. Python-only installs work.
- Private mode accepts only a dedicated exactly-read credential. Write/admin/mixed-scope tokens
  are rejected at login. Invalid/expired/tampered/wrong-audience cookies fail; removing a token
  record or rotating the signing key revokes sessions after rollout. Absolute expiry survives
  requests; logout clears local cookie without claiming server-side single-session revocation.
- Public/private MCP and private/Access dashboard host matrices pass, including wrong/repeated
  Host/Origin, spoofed forwarded headers, valid admin bearer, Access with write scope, encoded
  paths and catch-all behavior. Dashboard identities cannot invoke writes through any path.
- Access mode validates issuer/signature/audience/expiry/exact identity per request and rejects
  private cookie/bearer fallback. Existing public MCP assertion requirements remain unchanged.
- Cross-origin form/JSON/login/logout attempts, sibling-origin requests, framing and simple-CORS
  requests fail. Cookies have required attributes; GET does not mutate knowledge; POST query
  routes remain non-recording. Security and knowledge data never appear in logs or trace output.
- A spy/integration assertion proves one embedding provider object is shared and plain Portal
  queries never call it. Database is still accessible only from server services.

Rollback: disable flag/remove dashboard routes; core identity behavior remains compatible.
Update `SECURITY.md`, threat model and authentication/runbook proposals alongside code.

### PR3 — Explorer and deterministic Portal

Dependencies: PR2. Add locked React/TypeScript/Vite package, static asset integration, narrow
OpenAPI-derived client and route/components defined in the API/UI proposal. Implement login,
all/lexical/ranked browsing, filters, complete detail collection navigation, contexts/topics/facets,
explicit relations, as-of/known-at resolution and raw evidence fallback. No editor, export,
generated summary, remote content fetch, LLM or browser token store.

Acceptance:

- Browser can start at all-retained Explorer and reach every fixture assertion and every retained
  child page; Portal links map to the same filtered results. Ungrouped items remain reachable.
- Fixtures distinguish current/uncertain/disputed/superseded, proposed plans and temporal proposals,
  considered/rejected options, verified evidence and ingestion confirmations. No badge/rank/date
  promotes a proposal or rejected option to confirmed truth. Resolver conflict/ambiguous/unknown,
  missing classifications, precision and overflow remain visible.
- Source titles, malicious HTML/script/Markdown, command/file locators and executable-looking
  text artifacts render inertly; no external preview request occurs. Browser XSS/security tests
  and network request assertions pass.
- Component/Playwright tests exercise filter resets, next/back page navigation, stale-request
  cancellation, empty/degraded/error/401/429 states, session expiry/logout, temporal history
  continuation, mobile layout and keyboard/focus/label accessibility. Do not add snapshot-only
  tests that merely mirror component markup.
- `npm ci`, frontend formatting/lint, `tsc --noEmit`, production build, behavioral tests and
  Playwright pass with locked dependencies. Generated API types have no drift. No Node runtime,
  secrets, knowledge, public source maps or service worker is shipped.

Rollback: disable dashboard or revert static package; no database/data rollback needed.

### PR4 — Helm, release packaging and operational proof

Dependencies: PR3. Add `dashboard.enabled: false`, schema/env/Secret mapping, optional private
TLS Ingress, image build stage/core-only target, release/CI supply-chain integration and operator
documentation. Reassess actual merged Compose support before changing any standalone packaging;
none exists at this design baseline, so adding a Compose stack/profile is outside these PRs.

Acceptance:

- Default render has no dashboard routing/Secret requirements and no new public exposure. Enabled
  without ingress also has no outside route. Valid private and Access test configurations render;
  unsafe/overlapping hosts, incomplete secrets/TLS and auth downgrades fail closed.
- Helm lint/template, strict kubeconform and rendered-number checks pass for disabled/private/
  Access modes, default and reference values, and enabled/disabled existing Cloudflare profiles.
  Static/read-only-rootfs, resources/probes/capabilities/NetworkPolicies and core-only image work.
- Both application and backup images build, scan and satisfy existing release policy. Run the
  required backup/restore retention rehearsal because container/chart packaging changed. Before
  builds check shared Docker storage; afterward remove only new review images.
- Run full existing Python/pgvector/MCP/Inspector gates and new browser gates. At least 90%
  statement and 85% branch application coverage; no broad exclusions. Core flag-off behavior
  continues to pass. Locked builds include frontend dependencies in inventory/audits.
- Before any authorized live apply: server-side dry-run in Minikube. After authorized deployment:
  a real browser signs in through private TLS, browses all/Portal, opens sources/relationships,
  resolves temporal state and pages evidence. Concurrent first ranked searches share a single
  model load. Direct write attempts through dashboard fail while an existing authorized MCP
  client can still read/write on its own endpoint. Read API logs safely and check restart counts.
  Rollout/readiness or an in-Pod call alone is not proof. Report pending browser/deployment proof
  explicitly if operator authorization/configuration is unavailable.

Synchronize README, CHANGELOG, `docs/runbooks/minikube.md`, `cloudflare-access.md`,
`client-setup.md`, `monitoring.md`, `embeddings.md`, SECURITY/threat model and RELEASING where
their behavior changes. Add a dashboard runbook and independent security review/finding status
under `docs/reviews/`. Existing MCP docs/contract tests change only if a genuinely shared public
contract changes; no new MCP tools/flush behavior are planned. Keep Helm the deployment definition.

Rollback: disable dashboard flag and remove only its explicitly enabled routing, roll out the
previous verified image if needed, and preserve all core/worker/backup configuration. Sessions
may be invalidated by key rotation; no destructive migration rollback or knowledge rewrite.

## Verification strategy and evidence

Run narrow relevant tests first, then required gates from AGENTS.md: `uv lock --check`, Ruff
format/check, Pyright, unit/property/contract and real disposable pgvector integration/e2e tests,
coverage, Helm lint/render, strict kubeconform, rendered-number checks, supply-chain and Inspector.
Container/chart changes also require both image builds and backup/restore retention test.
New frontend/browser tests supplement rather than replace these gates. Tests never download
embedding models or read local `.env`; use explicit settings, deterministic embeddings and
synthetic knowledge. Reserve real client-path tests for an explicitly authorized deployment.

Property tests target cursor traversal/grouping invariants and scope/state separation. Integration
tests target actual SQL/filter behavior, read-only transactions/pool reuse, temporal overflow and
concurrent changes. Contract tests compare shared Python models, dashboard OpenAPI and generated
TypeScript; existing MCP/HTTP contracts remain unchanged. Browser tests target behavior, security,
authentication and accessibility across maintained Chromium/Firefox.

This design phase runs documentation/link/diff checks only. None of the above implementation,
browser, image, cluster, performance or security gates is claimed to have passed yet.

## Decision checkpoints

Maintainer accepts the proposed ADR before PR1. Confirm the bounded live-read scope (no complete
historical edit archive/snapshot export), exact-read login and separate hostname before PR2.
Before PR4 activation, operator supplies DNS/TLS/credentials/signing key and chooses private
deployment or explicitly approved Access exposure. Publishing, committing/pushing, cloud changes
and deployment are separate authorized actions; drafting this plan grants none of them.
