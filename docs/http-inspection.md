# Core read-only inspection API (protocol 1)

This server-to-server interface implements PR1 of [ADR 0009](decisions/0009-optional-read-only-dashboard.md).
It supports exhaustive retained-assertion inspection independently of the future optional
dashboard. It adds no UI, browser session, dashboard workload, public route, database or model.
Existing MCP and ordinary HTTP clients keep their contracts and normal usage accounting.

## Authentication and confinement

Set `KNOWLEDGE_VAULT_INSPECTION_PRINCIPALS` to a comma-separated list of **dedicated** principal
IDs, empty by default. Provision rotating bearer records for those IDs through the existing
token workflow with exactly `knowledge:read`; the token generator's read+write default is
unsuitable. Keep the issued read token in the calling service's Secret and its HMAC record in
the core's existing credential configuration. Never give a caller the core pepper, bootstrap
record Secret, writer/admin token or database credential. No credentials are issued by this PR.

API startup rejects absent records, mixed/write/admin scopes, reserved Cloudflare principal IDs and
credentials shared with an ordinary principal. Inspection identities are confined **before
HTTP/MCP routing** to the listed `/api/v1/read` operations. They cannot call ordinary reads
(which may record usage), ingestion, metadata, usage reporting, admin/deletion or MCP. The MCP
verifier also refuses them independently. Ordinary read/write/admin/Access identities cannot
use this inspection interface. No inspection principal is provisioned by default.

Use a fixed existing private core hostname over the existing ClusterIP Service, or authenticated
private HTTPS for a headless client outside the cluster. Host validation and public-Host Access
requirements remain in effect. Inspection requests require one bearer and one valid Host; Origin,
Cookie and Access assertion headers are refused. Do not publish a bearer-authenticated route.
A future BFF maps these operations explicitly and never forwards browser credentials or URLs.

Every read charges the shared READ rate budget for its service principal. Rate-limit errors
retain `Retry-After`. All inspection responses, including refusals, use `Cache-Control: no-store`.
Stored data is labelled untrusted; nothing is fetched from source URLs or executed.

## Operations

Base path `/api/v1/read`. OpenAPI at the existing internal `/api/openapi.json` describes exact
shared response models and inputs; this document describes semantics. No write operations exist.

| Method and suffix | Request | Result |
|---|---|---|
| `GET /capabilities` | None | Protocol `1`, fixed supported operation patterns and maximum page size |
| `POST /assertions/query` | `BrowseRequest` | Exhaustive ID-ordered `InspectionPage[AssertionSummary]` |
| `POST /catalog/query` | `CatalogRequest` | Context/topic/facet `CatalogPage`, exact distinct counts and state breakdowns |
| `POST /search` | Query, existing search filters, limit/cursor | Candidate-limited ranked summaries, scores and embedding degradation |
| `GET /assertions/{id}` | UUID | Full scalar assertion/provenance/usage metadata plus bounded child previews |
| `GET /assertions/{id}/sources` | limit/cursor | URL-ordered source rows, resumable beyond the ordinary 32-source preview |
| `GET /assertions/{id}/artifacts` | limit/cursor | UUID-ordered text artifact references without artifact bodies |
| `GET /assertions/{id}/identities` | limit/cursor | Distinct exact identities ordered by subject/property/scope/state-type tuple |
| `GET /assertions/{id}/relations` | limit/cursor | Both directions of typed links/supersession/conflict records, direct neighbors only |
| `POST /state/evidence/query` | Exact identity, optional known_at, limit/cursor | Raw immutable evidence and retirement marker; no winner selection |
| `POST /state/resolve` | Existing `ResolutionRequest` | Existing authoritative resolver, reasons/outcomes/history; unchanged evidence bound |
| `GET /contexts/{id}` | UUID | Stored context name/aliases |
| `GET /topic-aliases` | None | Existing bounded explicit alias mapping |
| `GET /artifacts/{id}` | offset/limit | Existing bounded artifact text JSON, never rendered/executed |

Data errors use existing problem-details/domain errors. Invalid/expired inspection cursors and
invalid bounds return 422; unconfigured/missing authentication returns 401, refused credentials
or paths return 403, unavailable operations 404/405, absent retained objects 404 and rate limits
429. Core public-Host authentication middleware can refuse a request earlier under its existing
policy. Database/service failures must not expose submitted data or SQL values in logs.

## Browse and catalog requests

```json
{
  "mode": "all",
  "filters": {"statuses": [], "missing_topic": false},
  "limit": 50,
  "cursor": null
}
```

All mode forbids query text and includes **all four statuses** by default. Lexical mode requires
nonempty query text and uses strict PostgreSQL `websearch_to_tsquery('simple', query)` with no
ranking cap, aliases in query text, semantic match or question fallback. Ranked `/search` remains
distinct, defaults to current evidence like existing search, and returns `candidate_limited:true`.
Exhausting a ranked cursor never means the whole corpus was enumerated.

Browse filters retain existing metadata semantics (AND across dimensions, OR within dimensions,
topic overlap with explicit alias expansion). Add `missing_topic`, `missing_context`, and
`missing_facet` for unclassified records; reject combining a missing flag with values for that
field. Valid-at filters retain permissive unknown bounds and are not historical state proof.
Review-due time is frozen at the cursor's first evaluation. Unknown/null metadata is not inferred.

Catalog request: `dimension` is contexts/topics/facets, with the same browse filters, optional
literal case-sensitive `prefix`, limit and cursor. Facets require context_id or explicit
`unscoped:true`. Canonical topics use one-hop stored aliases and deduplicate assertion IDs within
each group; `stored_labels` reports contributing stored labels. Empty classifications use null
key/“Uncategorized”; that cannot collide with stored values. Context UUIDs remain distinct even
with equal names; facets use exact stored keys. Counts cover all eligible records, not a ranked
candidate pool or current page. Topic counts overlap and are not additive. Catalogs sort by key,
never mutable count. No topic rewrite, clustering, ontology, translation or LLM summary is added.

## Pagination, history and budgets

Every inspection page returns `items`, `page_size`, `next_cursor`, `evaluated_at`,
`consistency:"live"` and `untrusted_data:true`. Limits default to 50, bounded by
`min(100, KNOWLEDGE_VAULT_MAX_PAGE_SIZE)`. Summaries contain at most 400 content characters and
an explicit truncation flag; fetch detail for full content. Queries select bounded projections,
not full ORM child collections or an entire corpus. Scalar DB aggregation computes catalog counts.

Detail's `source_page`, `artifact_page` and `identity_page` contain the first at most 20 items
(also shown in the legacy-shaped preview arrays). Follow each child cursor on its dedicated
route using the returned **page_size**, even when this differs from the endpoint's default.
UUID-bearing continuations remain valid after deletion of the boundary row. IDs/keys of related
objects are data, never a command to traverse recursively. Conflict records include `resolved_at`
so a retained resolved conflict is distinguishable from an unresolved one.

Versioned HMAC-signed cursors expire 30 minutes after their first evaluation and bind operation,
request/filter/query/page size, principal, canonical core Host audience and alias mapping digest.
Keep all parameters fixed while continuing; filter/alias/identity/known-at changes require a new
scan. Ranked continuations wrap the existing ranking cursor within this envelope. No cursor
contains assertion content. Source-key cursors can contain a stored URL; treat every cursor as
private and omit query strings/IDs from access logs in favor of fixed route labels.

Responses are capped at 2,000,000 serialized UTF-8 JSON bytes. Paged collections may return fewer
items than requested while preserving a continuation; an oversized single item/detail returns
an explicit 422 rather than silently omitting content. Resolver evaluation still includes the
whole eligible identity; only its returned history page can shrink to meet the byte bound.

Traversal is exhaustive for a **fixed** retained dataset. Each query has a statement snapshot;
there is no cross-request snapshot or export guarantee. Concurrent inserts before the boundary
can be missed until refresh; deletions disappear and updates can alter membership/counts. Never
interpret created/updated/confirmation timestamps or rank as real-world truth.

Raw evidence paging works even when an identity exceeds the resolver's 1,000-record bound.
`known_at` means evidence recorded by that time and retirement as of that time; absent known_at
is live. Resolution's separate `as_of` means real-world applicability. Resolver outcomes and
reason codes remain authoritative; no partial winner on overflow. Legacy supersession links,
immutable temporal history and current mutable revision metadata remain separate. There is no
complete edit archive, fabricated classification or recovery of hard-deleted knowledge.

## Enforcement and rollout

Core constructs a separate read-only **session factory**, sharing its engine/pool. A dedicated
session class sets `SET TRANSACTION READ ONLY` on every transaction, including implicit transactions
inside reused services. Ordinary writer sessions are unchanged; pool reuse must reset state.
Inspection search forces `purpose="maintenance"` and never records retrieval/use events. It reuses
the existing API embedding provider object; plain browse/catalogs never embed. No DB privilege or
model is given to a dashboard. This is not a new SELECT-only DB role or a replacement for route
and scope enforcement; the trusted core retains its existing ingestion credential.

Catalog transactions disable PostgreSQL JIT locally: the synthetic 50k query plan showed hundreds
of milliseconds spent compiling this bounded aggregate. This setting ends with that transaction;
ordinary API/worker/writer sessions keep their planner settings. Workers do not need API token
records merely because shared configuration carries inspection-principal IDs; record validation
is performed when constructing the HTTP application.

Ship this core API before a future BFF. No Helm template or live configuration is changed in PR1.
Operators later opt in to a dedicated inspection principal with ordinary credential/config rollout;
an existing unrelated principal must never be repurposed unknowingly because its routes become
confined. Revoke by removing its core token records/config through the normal workflow. Rollback
stops dependent clients before reverting the core image; no schema/data rollback is needed.
Disabling a future dashboard does not remove this independently useful core interface or tokens.

The single user/service can read every sensitivity class. Sensitivity is a filter, not authorization.
Selective sensitivity denial, browser login, dashboard image/resources and public exposure remain
outside PR1 and require their later accepted implementation/review gates.
