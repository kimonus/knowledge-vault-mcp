# PR #126 architecture review disposition

Status: documentation resolutions proposed for re-review; not reviewer acceptance or implemented
security proof. Review source: [comment 7003860752](https://github.com/kimonus/knowledge-vault-mcp/pull/126#issuecomment-7003860752)
on original design commit `59c789b`, retrieved 2026-11-19. All PR conversation comments, submitted
reviews and inline threads were requested: one conversation review comment, zero submitted
reviews and zero inline threads at assessment time. ADR remains **Proposed**; do not begin PR1
until the revised design is re-reviewed and accepted. This revision does not merge the PR.

## Findings and documentation resolutions

| Review finding | Resolution | Evidence / required future verification |
|---|---|---|
| **Blocking:** route flag in core is not independently deployable optional component | Separate Python SPA+BFF image, Dashboard Deployment and ClusterIP Service, only with `dashboard.enabled=true` | [ADR decision/rollback](../../decisions/0009-optional-read-only-dashboard.md), [runtime diagram/lifecycle](deployment-security.md); PR4 zero-resource and true→false gates |
| Core API/worker/image/runtime must not need UI assets/dependencies | Separate BFF package/lock/Dockerfile; no frontend stage or BFF/session import in core; additive generic core inspection API ships independently | [API layout](api-ui-contract.md), [PR1–PR3](implementation-plan.md); build core without web/Node/BFF artifacts |
| Disabled render must create zero dashboard resources; disabling removes only its resources | Guard every dashboard-owned object; no shared core ConfigMap/checksum/image changes; external Secrets and core read credentials remain operator-owned. Existing migration hook requires render-verified `--no-hooks` for dashboard-only upgrades on unchanged core | [Helm lifecycle](deployment-security.md), [PR4](implementation-plan.md); compare non-dashboard renders/checksums and rehearse removal with core clients |
| No Node runtime/database/LLM/model duplication | Static SPA with Python BFF, fixed HTTP read API; no DB role/engine/credentials/model volumes; ranked calls use existing API embedder | [ADR](../../decisions/0009-optional-read-only-dashboard.md), [security](deployment-security.md); import/image/egress and concurrent cold-search tests |
| Preserve dedicated hostname, opt-in ingress/private-by-default | Own Service and distinct validated browser host; ingress false by default; no DNS/tunnel change from enabling | [security Host/Helm sections](deployment-security.md), PR4 render matrix |
| Exact-read credentials; no browser write/admin bearer | Independent dashboard login HMAC records/pepper; separate server-only core service bearer; reject write/admin/mixed sets | [credential matrix/session rules](deployment-security.md), [contracts](api-ui-contract.md); negative auth tests at both hops |
| Server-side read allowlist; no browser access to core MCP API | Fixed BFF method/path mapping; core inspection principals confined to `/api/v1/read`, denied ordinary accounting reads/MCP/writes; browser cookie/login not a core identity | [HTTP mapping](api-ui-contract.md), PR1/PR2 actual-token confinement tests |
| No privileged credential or unrestricted DB role in dashboard; prefer least-privilege read interface | Selected HTTP interface with exact-read bearer. Dashboard has **no DB credentials/role** or core pepper/records; DB egress denied. Direct DB alternative rejected; would need SELECT-only grants, not transactions alone | [credential/DB isolation](deployment-security.md), PR2 mount/image and PR4 network-policy checks |
| Host/Origin, CSRF/CSP/no-store retained | Separate BFF Host validation, exact HTTPS Origin/custom headers/logout CSRF, strict cookie/CSP, inert text/no-store; no forwarded browser auth/Access headers to core | [security](deployment-security.md); adversarial browser/route tests in PR2/PR3 |
| Distinguish live keysets from snapshot-complete export | Fixed-dataset exhaustive iteration; concurrent inserts/updates/deletes can alter traversal; no snapshot export/full edit-audit guarantee | [browse/history contracts](api-ui-contract.md), PR1 concurrency tests |
| Uncategorized and all-status access | Default all four statuses and explicit missing-topic/context/facet filters/buckets; ranked mode labelled bounded | [Portal/browse contract](api-ui-contract.md), PR1/PR3 fixture coverage |
| Separate assertion status/evidence/workflow | Preserve independent enum fields, labels, kinds, confirmation metadata and resolver outcomes; no inferred confirmed truth | [epistemic presentation table](api-ui-contract.md), PR3 semantic fixture tests |
| Session revocation limitations explicit | Absolute 30-minute expiry, active login-record check, dashboard-only record/key rotation; logout alone does not revoke copied cookies; Access revocation belongs to provider | [session lifecycle](deployment-security.md), PR2 expiry/revocation tests |
| Sensitivity visibility explicit | Authenticated single user/service can read all classes; UI sensitivity filter is not an authorization boundary; selective denial needs new server policy | [risks/visibility](deployment-security.md), assumptions in [assessment](assessment.md) |
| Design-only and ADR not accepted | Only Markdown changes; no code/chart/deploy; four future PRs remain gated on re-review | [implementation plan](implementation-plan.md), git diff and PR status |

## Remaining risks and decisions

The extra BFF hop adds an availability/version boundary and consumes existing API read capacity;
protocol 1 compatibility check, bounded timeouts/concurrency and aggregate service rate limits
are proposed controls, not measured results. A compromised read-only BFF can read the entire
single-user vault; it cannot be granted write/DB authority. Stateless logout has no single-session
revocation; metadata cannot repair missing history or make live pagination a snapshot. No direct
DB fallback, additional database, LLM or dashboard model is proposed.

Maintainer must re-review/accept the ADR before implementation. Before enabling, operator supplies
private hostname/TLS, separate browser credential/pepper/signing Secrets, exact-read core inspection
identity and compatible core version, and accepts all-sensitivity visibility and session lifecycle.
Public Access/tunnel exposure remains a separate approval. Resource/query/cold-load budgets require
measurement during implementation. No operator response is required to finish this design revision.

## Validation scope

Documentation-only checks: local links, fenced blocks and JSON example syntax, whitespace/diff,
docs-only changed-file list, consistency of component/auth/network/model/lifecycle decisions across
all six design documents and ADR, and every finding mapped above. These checks do not prove the
future Helm render/removal, DB/network denial, browser security or test acceptance criteria.
No application tests, image builds, Helm changes, cluster operations or deployments are claimed.

Executed for this revision: seven Markdown documents checked; 72 local links resolve; six fenced
blocks close correctly; the one JSON example parses; final newlines/trailing whitespace and
`git diff --check` pass. Changed-file scope is exactly these seven documentation files. A targeted
scan found no remaining selected core-process deployment/route-flag architecture text. Manual
cross-document review mapped every review finding above and checked credential, model, Host,
network and true→false lifecycle consistency. No runtime/Helm/browser proof is implied.
