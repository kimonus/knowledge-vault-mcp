# Temporal evidence and order-independent resolution

## Audit and implementation plan

The existing application uses shared domain models, PostgreSQL/pgvector, SQLAlchemy services,
HTTP and MCP adapters, and a PostgreSQL embedding worker. Migrations 0001–0005 own the schema.
`assertions.content_hash` globally deduplicates normalized text. Begin/append/commit holds a
batch row lock, validates items independently, commits atomically, and retains retry receipts.
Concurrent content inserts retry the unique constraint. Enrichment merges sources/topics and
raises confidence, but retains the first assertion's observation and validity dates. Confirmation
timestamps are transaction time. Supersession mutates status and requires an existing predecessor.
Negation heuristics record possible conflicts; they do not identify exclusive properties.
Hybrid search filters mutable status, optionally filters validity (null bounds match everything),
and ranks text/vectors. It cannot establish current truth. Maintenance supplies reported/inferred/
verified evidence, historical roles, provenance, contexts/facets, and explicit revisioned edits.
None of these currently resolves out-of-order historical imports.

Reuse these services, authentication/scopes, secret checks, batch transactions, worker and generic
search. Do not reinterpret legacy status or rewrite existing assertion dates, text, IDs, sources,
or correction chains. No LLM, new dependency, worker duty, transport or infrastructure is needed.

## Selected design

Add optional structured `temporal` input and an immutable `temporal_evidence` table referencing
the existing deduplicated assertion. Each evidence record retains the validated atomic assertion
payload, original provenance/dates, a client-stable evidence key and its canonical payload digest.
This is an assertion evidence store, not a conversation store. Optional conversation/message
references identify sources without storing message bodies or conversation graphs.

Confirmed forgetting also writes content-free `temporal_retirements` for retained claims whose
correcting assertion is deleted. This prevents an invalidated claim from being reactivated by
deletion. A retirement references only the retained evidence key and time, never a deleted value,
deleted source key or content hash. It cascades when that retained evidence is itself forgotten.

Identity is the exact tuple (subject, property, scope, state type). Identifiers are explicit,
bounded strings, not inferred from similarity. No universal ontology or automatic alias merging:
clients reuse identifiers they have reviewed; ambiguous identity stays unstructured. Equal text
in different scopes can reference one legacy assertion but has independent evidence. Different
evidence keys retain independent confirmations, even for the same text/value. Same key and same
payload is a no-op across flushes; a changed payload is rejected without mutating that evidence.

Use a **read-time derived projection** over the complete indexed evidence set for one identity.
It is equivalent to synchronous reconciliation, with no materialized cache that can become stale.
One database SELECT supplies a consistent statement snapshot. Concurrent commits insert immutable
evidence; every read resolves all evidence visible to that snapshot. Stable-key advisory locks,
acquired in sorted order before row mutations, serialize reimports; existing content-race retries
remain. Whole-batch rollback includes evidence. No ingestion-order relationship is required.

Alternative: persist a current-state table and rebuild it under property locks after every write,
metadata edit and deletion. This improves very large-property reads but adds invalidation and
rollback complexity. Defer until measured demand; preserve a versioned pure resolver so a future
cache is rebuildable. A forward supersession chain alone is insufficient for backfills.

## Temporal rules

Transaction `created_at`/`updated_at`/`last_confirmed_at` never establish chronology. Observation
time means original source observation; validity means explicit real-world applicability. Keep
them separate. Precision is explicit (instant/day/month/year/unknown); comparisons must not order
overlapping precision windows. Observation basis distinguishes a message from a conversation.

Inputs distinguish snapshot, change, historical recollection, proposal, hypothesis and correction.
Proposals, hypotheses and historical recollections cannot become current merely by being newer.
Only reported/verified, positive-confidence, current assertions of supported stateful kinds can
confirm state. Inferred/unknown or disputed evidence cannot silently win. Confidence counts and
transaction timestamps do not break ties.

Explicit changes can end earlier open states when their reliable temporal anchors are strictly
ordered. Observation-only snapshots support "latest observed", not invented effective dates.
Overlapping explicit applicability of incompatible snapshots is a conflict. Different reliability
levels, equal/overlapping precision, undated competing values and unresolved correction references
remain explicit uncertainty. Explicit effective anchors take precedence over a discussion date.
Historical lookup never treats unknown bounds as proof that an assertion applied on that date.

Corrections reference stable evidence keys (including evidence imported later); they invalidate
the target claim, rather than imply a real-world transition. Without explicit replacement valid
dates they inherit the target's temporal applicability, while retaining their own discussion date.
Missing/cyclic/cross-identity targets are unresolved. Correction chains are evaluated from the
whole evidence set. `known_at` filters evidence transaction time before resolution, permitting
"what had the vault learned" queries; it is distinct from `as_of` real-world queries.

## API and compatibility

Keep all existing tools and default semantic search behavior. Add `resolve_knowledge_state` and
the matching HTTP route, using shared request/response models. It returns state, values, supporting
keys, reasons, conflicts, provenance-bearing history with pagination and resolver version. Clients
must use it for current/exact historical state, rather than treating legacy status/search rank as
truth. Get returns paginated evidence separately through the resolver; generic get/search remains
usable for unstructured knowledge. Commit receipts add evidence key/outcome per item. Temporal
inputs never use legacy supersedes_id: corrections and changes have distinct explicit semantics.
Metadata updates affect the legacy assertion, never rewrite immutable evidence; enrichment or
correction is a new evidence record.

The flush workflow must retain novel temporal evidence even when candidate text matches, capture
source precision honestly, avoid superseding configuration transitions, and route state questions
through the resolver. No caller instruction or retrieved content becomes trusted executable data.

## Migration, diagnostics and operations

0006 adds evidence/retirement tables, checks, FK cascades and exact lookup/key indexes; it never
rewrites assertions. An optional keyed request digest in flush receipts also makes one-call
correction retries return the original committed result, including concurrent retries, while
rejecting a changed payload after staging is purged. These receipts retain no reversible content
digest or content. Existing generic flush receipts retain null request digests.
Legacy rows remain unresolved until explicitly enriched with reviewed temporal evidence using the
normal flush path. Diagnostics count fully classified, partial and unresolved assertions without
exposing content. No automatic ontology assignment, fabricated dates or production data changes.
Deleting an assertion cascades its evidence; corrections with missing targets remain unresolved.
Fixed-label metrics record resolution outcomes; no knowledge values/identifiers enter logs/labels.
Bound total evidence per lookup and fail closed on overflow; paginate history without resolving
only a page. Rebuild consists of reading evidence for each identity and rerunning the pure resolver.

Before rollout: backup/restore rehearsal, full test gates, disposable legacy upgrade/downgrade,
Helm validation and staging authenticated MCP calls. Run the existing migration Job before new
Pods, refresh client schemas and skill. Roll back application images retaining expanded schema;
old clients can continue unstructured operations but cannot resolve temporal state. Do not downgrade
production schema to roll back code. A schema downgrade destroys new evidence, so requires a
verified backup and stopped writers. Existing assertion data survives downgrade. No live deploy
or production inspection is needed to implement/test this change.

## Verification plan

Pure unit/property tests cover permutations, precision, missing dates, reliability, interval
overlap, reconfirmations, corrections, cycles, historical recollections, and rebuild determinism.
Real disposable pgvector tests cover all 15 requested scenarios, concurrent commits, retries,
rollback, stable-key mismatch, scope separation, deletion, legacy diagnostics and migration safety.
Authenticated Streamable HTTP MCP tests ingest both orders, resolve current/history/known-at,
exercise correction and read/write scope checks, and verify existing client contracts.
Run narrow tests first, then formatter/lint/typing, full suites, statement/branch coverage and
chart gates. Report missing gates exactly. Record empirical results and remaining limitations
in the final implementation report after the implementation is exercised.
