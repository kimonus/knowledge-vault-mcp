# Knowledge maintenance

0.5.0 retains text assertions and legacy numeric confidence. `status` describes the record
lifecycle. Confidence 1.0 may be a legacy default; it is not verification.

| Field | Meaning |
|---|---|
| evidence_state | unknown (legacy default), reported, inferred, verified |
| temporal_role | unspecified (legacy default), ongoing state, historical observation |
| workflow_state | proposed/active/completed/cancelled for plans; unresolved/resolved for questions |
| last_verified_at | explicitly supplied verification time; verified requires a date and known source/provenance |
| review_after | due-for-review time, not expiry or permission to delete |
| context_id, facet | optional named subject/layout/project UUID and property key such as wifi.channel |
| provenance | bounded typed evidence references, never transcripts |
| revision | optimistic logical metadata revision, independent of access counts and embeddings |

Existing observed_at, source retrieved_at and validity dates remain available. Never invent exact
timestamps for approximate dates or copy creation time into observation time. To query retained
history at valid_at, explicitly include desired statuses, including superseded; default searches
still select current records. Missing validity dates are unknown bounds.

Provenance supports web/conversation/file/artifact/command/unknown, optional title/observation time
and a locator. Known provenance needs a locator; web references must be HTTP(S) without credentials;
artifact references are UUIDs also listed in artifact_ids. File/command locators are references,
not requests to read files or execute commands. No source is fetched. Legacy evidence stays unknown.

Create contexts with a chosen UUID, a descriptive name and up to 16 aliases. Identical retry is
safe; silent renaming is refused. Include descriptive context in claim text as well. A context left
unreferenced by explicit forgetting of its last assertions is deleted. Topic aliases are an
independent operator vocabulary, expand searches without rewriting claims, and cannot form chains.

Relationships are child -> parent part_of, dependent -> prerequisite depends_on, evidence -> claim
supports, symmetric equivalent_to, and question -> answer resolves. Part_of supports unique ordered
positions within a parent. Part/prerequisite cycles are refused transactionally. Related retrieval
returns direct links with a bounded limit and a truncated flag; fetch those IDs in maintenance mode.
Create/commit a question before linking its answer. Resolves changes its workflow; deleting the last
answer reopens it. Links do not replace supersedes_id, authorize merging, or imply verification.

## Repair metadata

Fetch with purpose=maintenance and review the revision, then call update_knowledge_metadata:

```json
{"request": {"assertion_id": "11111111-1111-4111-8111-111111111111",
 "expected_revision": 1, "idempotency_key": "chosen-opaque-key", "dry_run": true,
 "changes": {"confidence": 0.7, "evidence_state": "reported"}}}
```

HTTP POST /api/v1/metadata takes the inner request object. After reviewing the preview, repeat with
dry_run=false. A preview does not reserve a revision. Identical committed retries return the stored
outcome; stale revisions or keys reused with different input are refused. Sources, when supplied,
replace the URL-source list explicitly, including metadata for existing URLs. Null clears nullable
dates/context/facet/workflow but not required confidence/kind/origin. Changed text/meaning requires
supersession; superseded records cannot be reactivated by metadata status edits. Audit/retry data
keeps field names and hashes, not old/new assertion values, for 30 days and cascades on deletion.

Review queries return IDs/revisions/reasons and keyset next_after. Curation inspects at most 100
retained records within a supplied topic/context and returns advisory similar-text/competing-state
pairs, not an exhaustive audit. Inspect sources/context before explicitly replacing or linking them.
Disjoint known validity intervals do not constitute competing state. No production data is silently
merged, rewritten or deleted. Original numbered layouts and missing provenance remain gaps until
evidence supplies them.

## Access versus actual use

Usage aggregates start at zero/null: activity since tracking began, not lifetime access.
usage.available=false means accounting/readback failed; zero values are then not authoritative.
Normal searches count returned IDs once; a separate fetch counts another retrieval and one explicit
fetch. Candidate checks, linked inspection and purpose=maintenance reads do not count. Compatibility
search/fetch keep fixed inputs and default to normal; legacy-client audits cannot be distinguished
automatically. Counters describe service retrieval, not network delivery or human reading.

Focused search/get and HTTP search/get accept purpose normal/maintenance and optional UUID event_id.
Retry the identical logical request with the same event ID for up to 24 hours. Without an ID,
separate calls count separately. Retrying after receipt expiry may count again.

report_knowledge_usage (POST /api/v1/usage) accepts assertion_ids (1-100) and a UUID event_id. It
requires knowledge:write and records explicit actual use idempotently for 24 hours. Search/fetch
alone never reports actual use. Knowledge:read cannot assign counters or change evidence. Set
KNOWLEDGE_VAULT_USAGE_TRACKING_ENABLED=false (Helm config.usageTrackingEnabled=false) to disable
ordinary retrieval counting; explicit writer reports remain available. Tracking has short database
timeouts and content-free failure metrics; an accounting failure never prevents a valid read.
The worker purges expired receipts; there is no permanent query/click stream. No counter changes
confidence, verification, updated_at, revision, last_confirmed_at, ranking or deletion policy.
