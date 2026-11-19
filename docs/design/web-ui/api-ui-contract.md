# Proposed dashboard API and UI contracts

Status: accepted design for staged implementation; capabilities are not implemented by this document.
Read with [ADR 0009](../../decisions/0009-optional-read-only-dashboard.md),
the [assessment](assessment.md), and [security design](deployment-security.md).
All paths/settings/models here are proposals unless explicitly called existing. Public MCP and
existing `/api/v1` contracts remain compatible. Shared additions go in `domain/responses.py`;
transport-independent inputs/filters belong in domain modules, not React or route handlers.
The browser API runs in a separate dashboard Python BFF Deployment/ClusterIP Service; the core
does not serve SPA assets, sessions or browser routes.

## HTTP conventions

Base: `https://DASHBOARD_HOST/dashboard/api/v1`. JSON bodies use snake_case, timestamps are
RFC 3339 UTC, IDs are UUID strings, enums retain their stored values. Reject unknown input fields.
Every data route requires an authenticated, exactly read-only dashboard principal and charges a
local per-browser-principal read budget. Core separately charges the BFF's exact-read service
principal against its shared guard; do not describe this aggregate budget as browser-user attribution.
Login is separately rate limited before authentication. No browser credential
authenticates ordinary `/api/v1` or MCP. All responses containing knowledge use `Cache-Control:
no-store`, `Vary: Cookie`, and `untrusted_data: true`; no cross-origin read permissions.

Errors use existing RFC 9457-style `ProblemDetail` and `KnowledgeVaultError` subclasses: 401
missing/expired identity, 403 forbidden origin/authority, 404 absent retained resource, 405
disallowed method, 413 body too large, 422 invalid inputs/cursor, 429 with `Retry-After`, 503
unavailable core/database, 502 invalid/upstream failure and 504 upstream timeout. Never interpolate
submitted content, search text, upstream exception details or credentials into errors.
API 401 is JSON, not an HTML login redirect; top-level navigation may redirect to login/Access.
SPA fallback is restricted to known page routes and never handles `/api/`, static misses or auth.

Default page size 50, maximum `min(100, settings.max_page_size)`; reject out-of-range sizes.
Input query maximum 2,000 characters, filter limits reuse `SearchFilters`, and all child collections
are bounded. Use response summaries instead of multiplying 16,000-character content by page size.
No endpoint accepts table names, SQL fragments, arbitrary sort columns, caller-selected scopes,
`purpose`, usage `event_id`, or external URLs to fetch.

## Operation table

“New service” means a shared, transport-independent query, not duplicated database logic in a
browser adapter. GET is used for small identifiers; POST queries avoid placing private text and
filters in access-log URLs. POST here does not authorize a mutation.

There are two interfaces with one shared data contract:

- Browser → BFF: `https://DASHBOARD_HOST/dashboard/api/v1` (table below).
- BFF → existing core ClusterIP Service: `/api/v1/read` plus **the same listed data path and
  method**, e.g. browser `POST /dashboard/api/v1/catalog/query` maps only to core
  `POST /api/v1/read/catalog/query`. Session operations exist only in the BFF and never map upstream.

Core-only `GET /api/v1/read/capabilities` returns `{protocol_version:"1", operations:[fixed names],
max_page_size, untrusted_data:true}` under the same service authentication. The BFF uses it for
dependency/compatibility readiness, not a browser-selected proxy target. Unknown/incompatible
protocol or missing operations means dashboard data is unavailable; core remains operational.
Within protocol 1, additions are compatible; removing/renaming required fields/operations requires
a new protocol and explicit overlapping core compatibility before upgrading BFF.

The core read interface is a proposed additive, authenticated HTTP adapter over existing/shared
services, available to designated exact-read inspection clients even without a dashboard. It
does not require a dashboard flag, browser dependencies or extra model. It enforces a fixed read
operation allowlist, exact `{knowledge:read}` authority, forced maintenance purpose and read-only
DB transactions. Existing `/api/v1` and MCP behaviors remain unchanged for ordinary clients.

Configure designated inspection principal IDs independently of `dashboard.enabled`; only exact-read
bearer records for those IDs can use this interface. Core rejects those principals on every other
HTTP/MCP route, including ordinary reads that can record usage. Apply this check before normal
HTTP/MCP scope handling. Overlapping rotating records for an ID must all be exact-read. Access
identities and admin/write bearer records are not accepted by this service-only interface.
This is a route-bound use of existing scopes, not a new persisted scope or data model.

The BFF holds only its dedicated service bearer. It sends that bearer in a server-generated
Authorization header, with a fixed core Host, and never forwards browser cookies, Access JWTs,
Authorization, Origin, arbitrary headers or caller IDs. Core base URL and Host are operator
configuration, not browser input. Use the existing private core Host policy; internal HTTP stays
inside ClusterIP networking, HTTPS is required if this hop ever leaves the cluster. Do not use
the public Access hostname or disable core Host checks to make the BFF connection work.

Map each operation explicitly, validate bounded inputs/outputs on both sides, URL-encode UUIDs
and allowlisted path segments, disable upstream redirects, and never forward Set-Cookie/Location
or arbitrary upstream headers. Set a 2-second connect timeout, a bounded 60-second request budget
(including cold ranked search), bounded concurrency (initially 4) and a 2 MB JSON response cap.
Abort stale requests where possible; no unbounded retries/fan-out. Initial policy is no automatic
retry: propagate safe error/Retry-After and let the user retry. Core unavailability gives 503/504,
never direct DB fallback. These initial budgets require measurement before acceptance.
Page limits are upper bounds: core must return a smaller nonempty page with a valid continuation
when required to stay within the byte cap, especially for full temporal evidence payloads. Test
maximum-sized retained records; an oversized single item yields an explicit bounded error rather
than silent omission/truncation. Do not conflate an empty page with a transport-size failure.

| Method/path | Input | Response / reuse |
|---|---|---|
| `GET /session` | None | `{principal_id, auth_mode, expires_at, csrf_token, capabilities}`; capabilities enumerate fixed read operations, not privileged token/scopes; no knowledge |
| `POST /session/login` | `{credential}` plus same-origin login defenses | Private mode only: dashboard-local exact-read token verification, Set-Cookie; credential is never sent to core or echoed |
| `POST /session/logout` | CSRF header, no body | Private mode: 204 and expired cookie; Access mode: 204 with provider-logout guidance supplied by session metadata, not an invented local revocation claim |
| `POST /assertions/query` | `BrowseRequest` | New `AssertionPageResponse`; exhaustive enumeration or exhaustive strict lexical match mode |
| `POST /search` | `{query, filters, limit, cursor}` | Existing search service with forced maintenance purpose; map to bounded `RankedAssertionPageResponse`, retain score and degradation; explicitly limited ranked candidate universe |
| `GET /assertions/{id}` | UUID | Bounded `AssertionDetailResponse`; shared scalar metadata plus first pages of child references and their continuation tokens; forced maintenance purpose for existing fetch logic |
| `GET /assertions/{id}/sources` | `limit,cursor` | New paged `SourceView` rows ordered by URL with fixed DB collation |
| `GET /assertions/{id}/artifacts` | `limit,cursor` | New paged `ArtifactRef` rows ordered by UUID |
| `GET /assertions/{id}/identities` | `limit,cursor` | New distinct exact `StateIdentity` pages, ordered by canonical identity tuple |
| `GET /assertions/{id}/relations` | `limit,cursor` | New page of existing `LinkView`, legacy supersession edges and conflict records; deterministic edge-key ordering, direct neighbors only |
| `POST /catalog/query` | `CatalogRequest` | New `CatalogPageResponse` for contexts, topics or facets; distinct assertion counts and state breakdowns |
| `GET /contexts/{id}` | UUID | Existing `ContextResponse`, no implicit membership mutation |
| `GET /topic-aliases` | None | Existing `AliasResponse` (repository caps alias table at 1,000); no alias editing |
| `POST /state/resolve` | Existing `ResolutionRequest` | Existing `StateResolutionResponse`, including history token/reasons/version; resolve across all allowed evidence, never just the displayed page |
| `POST /state/evidence/query` | `{identity, known_at?, limit, cursor}` | New raw immutable `TemporalEvidencePageResponse`, with retirement marker, evidence key, assertion ID, recorded timestamp and retained validated payload; no computed winner |
| `GET /artifacts/{id}` | `offset,limit` | Existing `ArtifactContentResponse`; cap at service limits (UI requests 8,000 chars), render text inertly |

Only these operations are reachable through the dashboard host/API. Session commands affect
authentication, never knowledge. Do not expose statistics with operational/job details, curation,
review mutations, candidate-ingestion checks, writes or administrative deletion through the UI.
Portal reuses catalog and assertion queries; no independent stored portal documents/API are needed.

## Browse/filter contract

Illustrative request (synthetic input, not a real vault observation):

```json
{
  "mode": "all",
  "filters": {
    "statuses": [],
    "topics": ["backup"],
    "context_id": null,
    "evidence_states": [],
    "workflow_states": []
  },
  "limit": 50,
  "cursor": null
}
```

`BrowseRequest = {mode: "all"|"lexical", query?: string, filters: BrowseFilters,
limit: int, cursor?: string}`. `all` forbids query; `lexical` requires nonempty query and uses
strict PostgreSQL `websearch_to_tsquery('simple', query)` without semantic ranking or fallback.
This provides complete traversal of exact lexical matches when wanted; ranked search remains a
separate operation. Fixed `id ASC` sort initially avoids mutable timestamp/score ordering. A
future sort must be an explicit enum with a unique tie breaker and its own cursor version.

`BrowseFilters` reuses existing filter semantics but defaults `statuses=[]` (all). Fields in
different dimensions are ANDed, values within a dimension are ORed; topics use overlap with
canonical+alias expansion matching the existing one-hop alias model. Include an explicit
`missing_topic`, `missing_context`, or `missing_facet` boolean for Portal's unclassified buckets;
reject a missing-field flag combined with values for that same dimension. Do not overload `null`
or an empty list to mean unclassified. `review_due` is evaluated at a fixed timestamp carried by
the cursor, preventing clock-only membership changes while paging.

Response outline:

```text
AssertionPageResponse {
  items: AssertionSummary[],
  page_size: integer,
  next_cursor: string|null,
  consistency: "live",
  evaluated_at: timestamp,
  untrusted_data: true
}
AssertionSummary {
  id, revision, excerpt: string[<=400], content_truncated: boolean,
  kind, origin, status, confidence, sensitivity,
  topics, context_id, facet, evidence_state, temporal_role, workflow_state,
  created_at, updated_at, observed_at, valid_from, valid_to,
  last_confirmed_at, confirmation_count
}
```

No total is required for assertion traversal; display loaded-item count, not an invented total.
Use indexed keyset predicates, fetch at most `limit+1`, no OFFSET/full Python corpus load. The
new cursor is signed and versioned, binds operation, normalized filters, alias mapping digest,
query mode/text hash, page size, principal, host audience and final immutable ID. Expire after
30 minutes. Reject tampering, other-query reuse, alias changes, unknown versions and expiry;
prompt a refresh. Never put assertion content in a cursor.

Core signs and validates data cursors using the designated service principal and core audience;
the BFF treats them as opaque and never needs the core signing key/pepper. Browser sessions must
be reauthorized before forwarding each page request. The single-user design deliberately shares
one core read view between sessions; this is not a per-session data permission boundary. Ranked
cursors reuse core ranking semantics; reset on core rollout and keep page size fixed.

Implemented core inspection page models expose `page_size`, including detail's `source_page`,
`artifact_page` and `identity_page`. Continue those preview cursors with their returned page size,
which can differ from the route default. See [the core API reference](../../http-inspection.md)
for exact runtime names, limits and error semantics; the future browser DTO names here remain design.

This is a **live** scan: a fixed dataset is visited exactly once to exhaustion; page queries use
statement snapshots, not a transaction spanning HTTP requests. Concurrent additions with smaller
UUIDs can be missed until refresh; deletions disappear; updates may alter filter membership or
counts. Do not claim point-in-time consistency, completeness under concurrent mutation, or a
historical export. A long-lived snapshot protocol is outside this scope and would need a new
design if required. Deleting the boundary row must not invalidate the cursor's keyset predicate.

Ranked responses use these summaries with scores, `embedding_degraded`, `next_cursor`, and
`candidate_limited: true`. Never label an exhausted ranked cursor “all matching knowledge.”
Keep page size fixed through ranked pagination because it controls candidate bounds. Restart
ranked pagination on query/filter/rollout changes; its ordering is not a snapshot either.

## Detail, relationships and time

Detail retains **all assertion scalar fields** from `AssertionResponse`, including bounded
provenance metadata and usage availability/counters, but uses separately paged sources,
artifacts and identities. Do not blindly serialize unbounded ORM relationships or call an eager
loader that reads the entire collection. Extract a reusable bounded projection from existing
fetch logic rather than modifying old clients' payloads accidentally. Each preview explicitly
has `next_cursor` and collection total or `has_more`; source truncation must never be silent.
Provenance array is already bounded in the domain; render all its stored fields as data.

Relations return typed envelopes:

```text
RelationPageResponse {
  items: [
    {type: "link", edge: LinkView} |
    {type: "supersession", predecessor_id, successor_id} |
    {type: "conflict", conflict: ConflictView}
  ],
  next_cursor, untrusted_data: true
}
```

Query both ends of typed links, both directions of supersession, and both sides of recorded
conflicts. Preserve direction: `part_of` source is child/target is parent; `resolves` source is
question/target is answer. Sort by fixed type then unique tuple (or conflict UUID), with `position`
used within `part_of` displays. Fetch neighbors only when expanded, with a visited-ID set in UI
navigation. Never infer “rejected by,” “caused,” “newer truth” or transitive equivalence. Missing
or deleted neighbors show unavailable; no content is retained in browser storage for recovery.

History comprises three clearly separate areas:

1. Retained legacy predecessors/successors, traversed by bounded adjacency requests.
2. Structured temporal evidence for a selected exact identity, with original precision/basis,
   disposition and resolver reasons. `as_of` controls real-world time; `known_at` controls when
   evidence was recorded. Changing either resets history pagination. Follow
   `next_history_after` with exactly the same resolver request parameters.
3. Assertion creation/update/confirmation timestamps and current revision. Label this metadata,
   **not a complete edit audit**. No history can be fabricated for unclassified legacy content.

The raw evidence query keysets by evidence key using fixed collation, binds identity and
`known_at` to its cursor, and indicates retirement as of `known_at` (or now). It applies no
status/topic filters that would silently omit competing evidence. It is available even if
resolution overflows; display the existing 422 overflow as “state unresolved: evidence limit,”
offer paged raw history, and never choose a partial winner. Resolver history is key-ordered,
not guaranteed chronological. A UI timeline can sort only its visible page and must say so;
prefer a paginated evidence table initially. Identity discovery also continues beyond the
existing 32-preview cap. Global identity autocomplete is not required.

## Deterministic Portal projection

`CatalogRequest = {dimension: "contexts"|"topics"|"facets", filters: BrowseFilters,
prefix?: string[<=80], limit, cursor}`. A facet catalog requires context_id or an explicit
unscoped selection. Return `{items:[{key,label,assertion_count,by_status,by_kind,
by_evidence_state,by_workflow_state}],next_cursor,grouping_version:"1",evaluated_at,
consistency:"live",untrusted_data:true}`. Counts use distinct assertion IDs after **the same**
filter and alias expansion rules as the corresponding browse query, across all eligible records.
Catalog cursor orders by stable canonical key, not mutable count. Use fixed database collation
for ordering; display localized labels without changing key identity. Include a tagged missing
bucket whose key cannot collide with an actual topic/facet/context value.

Algorithm version 1:

1. Contexts use stored UUID/name/aliases. Same-name contexts remain distinct. No inferred entity
   merging, substring clustering or labels generated from assertion text.
2. Topic membership maps each normalized stored topic through the existing explicit one-hop
   alias table. Group on canonical topic, deduplicate assertion IDs within each group, expose
   contributing stored labels. Never rewrite the original assertion's topics. No stemming,
   translation, embedding clustering, prefix hierarchy or arbitrary alias chains.
3. Facets are exact stored facet keys within the selected context (or explicit unscoped view).
   Missing contexts/topics/facets remain reachable; records may appear under several topics.
   Counts are not additive across topics or overlapping navigation paths.
4. A thematic page is a filtered browse query. Within it, provide switchable sections/filters
   for decisions/configuration/procedures, plans, questions, considered options, rejected
   options and other kinds. Keep status/evidence/workflow badges on every card. These are
   deterministic labels on sets, not a generated prose summary.
5. Relationships enrich direct assertion navigation. `part_of`/`depends_on` and other stored
   link kinds appear with direction and position where provided. They never silently add
   neighbors to a theme's membership; cross-theme links remain explicit links. No recursive
   graph expansion or transitive closure is needed.

Limitations: sparse/noisy topics yield sparse/noisy navigation; synonyms without aliases stay
separate; multiple languages are not translated; orphaned/missing classifications remain visible;
high-degree relations require paging; alias edits regroup pages on refresh; live counts may
change between requests. Grouping demonstrates organization, not epistemic support. Portal
has no editable/generated wiki pages and no persistent projection cache.

## UI routes and component boundaries

Browser routes (under `/dashboard/`):

| Route | Purpose |
|---|---|
| `/login` | Private credential exchange; Access mode uses provider navigation |
| `/explorer` | Default all-retained browse, filters, pagination; switch all/lexical/ranked |
| `/assertions/:id` | Full detail, sources, relationships and history tabs |
| `/portal` | Context/topic catalogs, unclassified navigation and state breakdowns |
| `/portal/contexts/:id` | Stored context detail, facet catalog, filtered assertion sections |
| `/portal/topics/:encodedTopic` | Canonical topic and alias explanation, filtered assertions |
| `/state` | Selected exact identity, as-of/known-at controls and evidence pages |

Private search terms, filters, provenance and credentials stay out of query strings/history;
request bodies and in-memory route state carry them. Deep links contain only selected resource
keys/UUIDs (still private; scrub access logs and use `Referrer-Policy: no-referrer`). No requirement
to share filtered result URLs. A reload resets transient filters; show that behavior clearly.

Proposed layout, illustrative paths only:

```text
web/
  package.json, package-lock.json, tsconfig.json, vite.config.ts
  src/
    app/                 # app shell, router, auth boundary, error boundary
    api/                 # generated OpenAPI types, narrow fetch client, abort/cursor handling
    explorer/            # ExplorerPage, FilterPanel, AssertionList, PageControls
    assertion/           # AssertionPage, MetadataPanel, ProvenancePanel, ArtifactText,
                         # RelationsPanel, SupersessionPanel, TemporalEvidencePanel
    portal/              # PortalPage, ContextPage, TopicPage, CatalogList, ThemeSections
    state/               # StatePage, IdentityPicker, TimeControls, ResolutionResult
    shared/              # StatusBadges, EvidenceBadges, EmptyState, ErrorState, LoadingState
    styles/
  tests/                 # component/behavior tests and Playwright flows
dashboard/                            # separate Python package, lockfile, tests and Dockerfile
  src/knowledge_vault_dashboard/       # FastAPI BFF, host/auth/session and fixed HTTP client
src/knowledge_vault/api/read.py        # proposed core inspection adapter, no browser routes
src/knowledge_vault/services/browse.py # proposed shared bounded queries/catalog projections
```

Use semantic HTML, keyboard navigation, labelled controls, focus restoration, screen-reader
announcements for loaded pages and text labels alongside color. Use 50-row pages rather than an
unbounded infinite scroll or a graph canvas. Render content and artifacts as escaped plain text;
no HTML/Markdown renderer in the initial scope. Allow only HTTP(S) source links; file, command,
conversation and unknown provenance locators are inert text. External links use noopener and
noreferrer; do not fetch previews/images. Display exact timestamps with UTC and an optional local
presentation. Treat low precision as a range/label, never an invented precise instant.

Keep auth/query data only in memory, clear it on logout/401, abort stale requests on navigation,
and never persist a service worker, offline cache, telemetry payload or vault snapshot. Minimal
React state/hooks and fetch suffice; add a query-state library only for a demonstrated need.
Generate TypeScript shapes from reviewed dashboard OpenAPI during build/CI, not at runtime.
Core read and BFF data schemas must agree; compatibility tests detect drift. Package the BFF with
only its minimal Python HTTP/auth/runtime dependencies, reusing the existing conventions and
reviewed pure auth helpers; never import/build the core Container or model/DB dependencies to
serve the dashboard. Node and browser dependencies stay in the dashboard build, not core builds.

## Epistemic presentation acceptance

| Stored dimension | Required presentation |
|---|---|
| `status=current` | “Current evidence”; no automatic “confirmed truth” badge |
| `uncertain` / `disputed` / `superseded` | Separate textual badges, filterable and never hidden from all-retained mode |
| `kind=considered_option` / `rejected_option` | Explicit considered/rejected label even if status is current |
| Plan `workflow_state=proposed` | “Proposed plan”; active/completed/cancelled remain separate |
| Temporal `mode=proposal` / `hypothesis` | Display in evidence view as proposal/hypothesis, never current resolved state by recency |
| `evidence_state=verified` | “Verified evidence” plus verification date and provenance; no inferred verification |
| `confirmation_count`, `last_confirmed_at` | “Ingestion confirmations”; clarify they do not count independent sources |
| Resolver `resolved` | Show exact identity, times, supporting keys, reasons and resolver version; distinguish ambiguous/conflict/unknown |

Unknown/null metadata stays unknown. Do not collapse these dimensions into one “confidence” pill
or invent a confirmed status absent from the data model.
