# Optional Web UI: repository assessment

Design assessment, 2026-11-19, baseline `39b6af7c27950a7e57159b59cc7157ff8728f248`.
Facts below are verified from this checkout, not proof of live deployment behavior. The
[ADR](../../decisions/0009-optional-read-only-dashboard.md) is **accepted for staged implementation**. This package implements
no application, endpoint, Helm setting or authentication change.
The revision responds to [PR #126's review](https://github.com/kimonus/knowledge-vault-mcp/pull/126#issuecomment-7003860752)
of `59c789b`. Verified facts below retain the original baseline; the separate dashboard runtime
and core read interface described afterward are proposals, not existing capabilities.

## Verified repository capabilities

| Area | Inspected evidence | Finding and implication |
|---|---|---|
| Runtime/services | [container.py](../../../src/knowledge_vault/container.py), [app.py](../../../src/knowledge_vault/api/app.py) | FastAPI and MCP share ingestion, search, administration, artifacts, maintenance, usage, temporal and operations services. Domain/services do not require browser request objects. Reuse them. |
| HTTP contracts | [schemas.py](../../../src/knowledge_vault/api/schemas.py), [responses.py](../../../src/knowledge_vault/domain/responses.py) | Versioned HTTP API, generated OpenAPI, shared typed responses, problem-details errors. New response shapes belong in shared domain responses. |
| Search | [search.py](../../../src/knowledge_vault/services/search.py) | Nonempty query, PostgreSQL `simple` FTS plus same-model vectors, deterministic rank fusion, multilingual/question fallbacks and embedding degradation. Candidate limit per ranking source is `min(max(limit*5,50),250)`. Pagination only traverses fused candidates; it is not a corpus iterator. |
| Filters | [SearchFilters](../../../src/knowledge_vault/domain/models.py) | Kind, origin, statuses (default current; empty means all), topics (overlap/OR with aliases), sensitivity, minimum confidence, validity-at, creation range, context, facet, evidence/temporal/workflow states and review-due. Different dimensions combine with AND. Null validity bounds are permissive search filters, not historical proof. |
| Cursors | [cursors.py](../../../src/knowledge_vault/services/cursors.py) | Signed query/filter-bound score+UUID cursor. Ranking can change across updates/rollouts. No persisted result snapshot. Do not reuse it for exhaustive browsing. |
| Fetch | [search.py](../../../src/knowledge_vault/services/search.py) | Full assertion scalars and metadata, sources and artifact references. Source output silently caps at 32; identities cap at 32 with a truncation flag. Source loading can still read more rows than are returned. Artifact references are not paginated in the view. |
| Usage side effects | `SearchService._account` | Normal search/fetch can persist retrieval/fetch counters; `purpose="maintenance"` skips recording and still reads usage. This must be forced server-side for dashboard requests. |
| Authentication | [api/auth.py](../../../src/knowledge_vault/api/auth.py), [auth/tokens.py](../../../src/knowledge_vault/auth/tokens.py) | HMAC token records, constant-time comparisons, overlapping records, read/write/admin scopes; admin permits all scopes. `require_scope` also charges shared principal rate budgets. No browser session/login exists. |
| Access/Host/Origin | [middleware.py](../../../src/knowledge_vault/api/middleware.py), [cloudflare.py](../../../src/knowledge_vault/auth/cloudflare.py) | Origin verifies JWT signature, required claims, issuer, audience, expiry and allowed email. Public/private Host policy fails closed when configured. Access defaults to read+write. Origin validation specifically covers `/mcp`; it does not provide dashboard CSRF protection. CORS is disabled unless configured. |
| Knowledge metadata | [maintenance.py](../../../src/knowledge_vault/domain/maintenance.py), [enums.py](../../../src/knowledge_vault/domain/enums.py) | Four assertion statuses; separate evidence/workflow/temporal fields, contexts, facets, provenance, topic aliases and typed relations. No universal taxonomy or generic confirmed status exists. |
| Temporal state | [temporal.py](../../../src/knowledge_vault/services/temporal.py), [temporal contract](../../../src/knowledge_vault/domain/temporal.py) | Exact `(subject, property, scope, state_type)` identity; separate real-world `as_of` and recorded `known_at`; bounded paginated evidence; outcomes resolved/ambiguous/conflict/unknown. Identity-wide resolution refuses over 1,000 evidence records rather than returning a partial winner. |
| Persistence/history | [tables.py](../../../src/knowledge_vault/persistence/tables.py), [data model](../../data-model.md) | PostgreSQL source of truth, immutable temporal evidence/retirements, explicit supersedes pointer, mutable assertion metadata/revision. Revision is not an event log or recoverable snapshot of every prior edit. Hard deletion removes knowledge/evidence; UI cannot show forgotten content. |
| Packaging | [pyproject.toml](../../../pyproject.toml), [Dockerfile](../../../Dockerfile) | Python/uv project, one application image for API/worker; no tracked frontend package manifest or JavaScript lockfile. The API's first semantic search can load a model, with memory implications. |
| Helm | [values.yaml](../../../charts/knowledge-vault/values.yaml), [templates](../../../charts/knowledge-vault/templates) | Single chart; API/worker/migration, optional internal PostgreSQL, backups, Access and tunnel. ClusterIP origin; ingress/tunnel disabled by default. Ingress defaults expose `/mcp`, `/api/v1`, `/.well-known`, not `/`. No dashboard value exists. `extraObjects` is the environment-specific extension seam. |
| Upgrade hooks | [job-migration.yaml](../../../charts/knowledge-vault/templates/job-migration.yaml) | Migration Job is an unconditional `post-install,post-upgrade` hook. A normal full-chart upgrade re-runs it even if only UI values change. Dashboard-only changes need the documented render-verified `--no-hooks` procedure on an already compatible core release. |
| Isolation | [networkpolicy.yaml](../../../charts/knowledge-vault/templates/networkpolicy.yaml), [API Deployment](../../../charts/knowledge-vault/templates/deployment-api.yaml) | NetworkPolicies, non-root/read-only rootfs, dropped capabilities and resource limits exist. Default API ingress peers include any namespace; this is not a browser authorization boundary. Shared origin DB access does not imply browser DB access. |
| Checks | [ci.yml](../../../.github/workflows/ci.yml), [AGENTS.md](../../../AGENTS.md) | Ruff, Pyright, real PostgreSQL tests, coverage, supply-chain, Kubernetes and Inspector gates. No frontend gate exists yet. Maintain 90% statements/85% branches. |

## Existing HTTP inventory

Paths are relative to `/api/v1`. “Read” describes authorization; search/fetch can still write
usage unless maintenance purpose is selected. The dashboard must not proxy this entire inventory.

| Scope | Existing methods and paths |
|---|---|
| Read | `POST /search`, `POST /candidates/check`, `GET /assertions/{id}`, `GET /artifacts/{id}` (offset/limit text pages) |
| Read | `POST /state/resolve`, `GET /state/diagnostics`, `GET /statistics`, `GET /conflicts` (limit only) |
| Read | `GET /contexts/{id}`, `GET /assertions/{id}/related` (limit + truncation only), `GET /topic-aliases` (bounded 1,000 aliases), `GET /review` (UUID continuation), `GET /curation-candidates` |
| Write | `POST /flushes`, `PUT /flushes/{id}/parts/{number}`, `POST /flushes/{id}/commit`, `POST /flushes/{id}/abort` |
| Write | `POST /artifacts`, `PUT /artifacts/{id}/chunks/{number}`, `POST /artifacts/{id}/commit`, `POST /corrections` |
| Write | `POST /metadata`, `POST /contexts`, `POST /links`, `POST /topic-aliases`, `POST /usage` |
| Admin | `POST /forget/preview`, `POST /forget/confirm`, `POST /state/evidence/attach` |

The application also mounts stateful Streamable HTTP MCP at `/mcp`, health probes, metrics and
OpenAPI. These do not become dashboard routes. No additional MCP tool is necessary for the UI.

## Additional capabilities genuinely needed

1. Exhaustive filtered assertion enumeration, without query text or ranking candidate limits.
2. Paged context/topic/facet catalogs with exact distinct-assertion counts for the selected scope;
   Portal grouping must use the whole eligible corpus, not the current search page.
3. Resumable detail collections: all source rows, artifact references, temporal identities, typed
   adjacent links, reverse supersession, and per-assertion conflict records. Existing detail/related
   endpoints cannot enumerate all of these. No recursive graph service is necessary.
4. Raw evidence pagination by exact identity independent of the resolver's 1,000-item bound, so
   history remains inspectable when state cannot be resolved. Existing resolver remains authoritative.
5. A service-authenticated core read HTTP interface with route-bound exact-read inspection
   principals, non-recording reads and bounded shared DTOs; independently useful without a UI.
6. A separate Python dashboard BFF/image/Deployment/Service, local browser session boundary and
   fixed client for that core interface; no DB credentials, model or core privileged auth material.

Existing context detail, alias rules, artifact text paging and temporal resolution can be adapted
without new business semantics. Do not add a summary writer, topic editor, public sharing, bulk
export, arbitrary SQL endpoint, global graph traversal, live policy API or metadata editing.

## Revised component boundary (proposal)

PR #126 rejected core-process route registration as insufficient optionality. The chosen
dashboard is an independently versioned static-SPA/Python-BFF image with its own conditional
Deployment, ClusterIP Service, ServiceAccount, ConfigMap and NetworkPolicies. An opt-in private
TLS Ingress targets that Service. All its resources disappear on disable; core resource renders
and Pod-template checksums are invariant across dashboard values. External Secrets/core service
records remain operator-owned. Core builds/runtimes do not require the frontend/BFF.

The proposed core `/api/v1/read` interface reuses existing services and API embedder. It accepts
only designated exact-read inspection bearers and confines them to non-recording read operations.
The BFF uses one issued read bearer, separate browser credential/pepper/session Secrets, and a
fixed HTTP operation mapping. It has neither an unrestricted nor a SELECT-only DB role: no DB
connection is supplied. This chooses the review's least-privilege-interface alternative; the
rejected direct-DB alternative would require database-enforced SELECT-only privileges. New
inspection API/route restrictions are not verified repository facts. See the
[review disposition](review-response.md) for every finding and its documentation resolution.

## Compose assessment and assumptions

`git ls-files '*compose*' '*podman*' '*package.json' '*lock*'` returns only `uv.lock` at this
baseline. File searches and searches of README/docs/workflows found no Compose/Podman deployment
instructions or configuration. Existing local worktree names mentioning standalone/Compose are
not evidence that those branches are merged or supported. No Compose engine was exercised.
Do not add a profile integration or claim Docker/Podman compatibility in this phase. Reassess
the then-current main branch before PR4; if standalone support has merged, integrate with its
actual private-network/secret conventions in a separate scoped follow-up. Minikube remains the
reference deployment and database-validation path.

Assumptions requiring product agreement: single user, desktop/mobile browser, complete retained
knowledge visible to that user (including sensitive items), read-only means no knowledge or usage
writes, explicit credentials/settings may change outside the UI, no historical recovery beyond
retained evidence, and live pagination rather than a cross-request snapshot/export guarantee.
No real corpus size, latency target or browser performance has been measured.
