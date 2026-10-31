# Golden flush cases

These cases specify observable decisions and tool-call order. Values such as UUIDs and idempotency
keys are illustrative.

## Exact trigger

User: `flush knowledge to my MCP`

Expected: extract every meaningful assertion in available context, then call:

```text
check_knowledge_candidates(candidates=[every planned assertion text])
begin_knowledge_flush(key=K, declared_parts=N, declared_items=M)
append_knowledge(batch_id=B, part_number=1, assertions=[...])
...
append_knowledge(batch_id=B, part_number=N, assertions=[...])
commit_knowledge_flush(batch_id=B)
get_knowledge(assertion_id=ID)   # only for IDs in readback_ids; usually none
```

`M` counts only what the vault does not already hold. Report server commit counts, assertions
skipped as already stored, records read back, rejections and omissions. Disclose context compaction
if known.

## Incremental flush

Context yields 25 assertions about a home network. One `check_knowledge_candidates` call with the
25 texts shows that 18 are already stored, 4 are new, 2 add detail to stored assertions (a channel width
for a stored channel, a firmware version for a stored model), and 1 contradicts a stored assertion:
the access point moved from channel 36 to 44 during the conversation.

Submit 7 items: the 4 new ones, the 2 added details as their own assertions, and "The access point
uses channel 44." with `supersedes_id` of the stored channel assertion. Do not resubmit the 18.
Commit returns `inserted: 7`, `superseded: 1`, seven `items` with outcome `inserted`, one of them
with `superseded_id`, and an empty `readback_ids`. Nothing is read back: not the new records and
not the correction. Report 7 stored, 1 superseded, and 18 already
stored with their IDs.

Do not submit all 25 reworded: the server would insert 25 records, leave the outdated channel
current, and report no conflict.

## Reaffirmed and enriched

The conversation re-read the router's configuration and confirmed a stored assertion, and it found
a documentation URL for another. Submit both with `content` copied exactly from the stored
records, the second with the new source. Commit returns `confirmed_existing` and
`enriched_updated`. An entry in `ignored_fields` means the stored record kept its own value for
that field; its ID is then in `readback_ids`. Read that record once and report the difference.

## Reconciliation unavailable

`check_knowledge_candidates` fails after a retry. Flush the whole extraction, and report that
reconciliation was skipped and duplicates of stored assertions may have been created. Never
withhold knowledge because the check failed. When the result has `embedding_degraded: true`, the
matches were found by shared words only and carry no similarity; use them the same way.

## Natural variants

`save what we learned to my knowledge MCP`, `persist this conversation's useful knowledge in my
MCP`, and `flush our findings into the knowledge vault` follow the same sequence. `Summarize what we
learned` and `what does MCP mean?` do not invoke ingestion.

## Partial retry

If part 3 times out after submission, retry part 3 with the same `B`, part number, and byte-equivalent
assertion data. Do not begin a new flush. Continue with part 4 only after the retry confirms part 3.
If commit times out, retry commit with `B`.

## Duplicate flush

If the same logical flush is retried, reuse `K`. Begin returns the original `B`; accepted parts and
commit replay their prior safe results. Report `confirmed existing` exactly as returned—do not claim
new inserts.

## Secret-containing input

Input includes a real API token and the useful fact that it lives in Kubernetes Secret
`vault-auth`. Exclude the token value. Submit only the secret reference. If a secret-shaped item is
rejected, report the rejection; never retry the secret value in another form.

## Long context

Extract all assertions first, calculate exact `M`, then split into bounded parts. Do not truncate to
one part or declare totals before completing extraction. If older context is unavailable because of
compaction, flush what is available and disclose the limitation.

## Source coverage and partial material

The visible context contains a user correction, an attachment whose first section was read but
whose remainder was not available, and several conversational acknowledgments. Keep local source
references S1 for the correction, S2 for the accessible attachment section, S3 for its unavailable
remainder, and S4 for grouped acknowledgments. S1 is reviewed and supports two planned claims; S2
is partially reviewed and supports one; S3 is unavailable; S4 is reviewed with zero durable claims.
Submit only the useful claims, after the usual candidate check. Do not submit S1–S4, the ledger,
raw passages or invented message IDs as assertions or provenance. Report two reviewed segments,
one partial, one unavailable, three planned claims and the unresolved attachment remainder. A
successful commit does not make S3 processed or prove that S2's extraction was exhaustive.

If a source was visible but the client failed to inspect it, mark it **unprocessed** rather than
reviewed. If no durable claim was found after inspection, mark it reviewed with zero claims, not
unprocessed. If the chat was compacted, count only currently visible segments and explicitly say
that earlier context could not be audited. On a retry of the same logical flush, reuse its key and
payload; do not create a second source receipt from the local ledger.

## Failure

If any declared part never succeeds, do not call commit. State: the flush is incomplete and the chat
should not yet be deleted. If commit returns an error, retry only when transient; otherwise give the
same incomplete warning.

## Exact reproducible detail

Context describes a recipe using 300 g flour, 210 g water, 6 g salt, 1 g yeast, kneading for
8 minutes, fermenting for 12 hours at 20°C, and baking for 25 minutes at 230°C after preheating.
It rejects a 30-minute bake because it burned the crust. Store the complete reproducible procedure
and the rejected alternative with its reason as independently useful records. Do not replace the
recipe with "make bread using a long fermentation". Before append, compare quantities, units,
temperatures, timing, ordering and the rejected alternative with the context; an `inserted` item is
stored exactly as submitted.

## Readback failure after commit

Commit succeeds with two IDs in `readback_ids`. The first read succeeds; the second returns a
transient error.
Retry the second read, respecting rate limits. Do not repeat begin/append or create another flush
because of the read failure. If verification cannot finish, report "committed, verification
incomplete", one record verified, and the unchecked ID. Do not claim that nothing was saved.

## Duplicate IDs and rejected items

Three submitted items include two exact normalized duplicates and one secret-shaped rejection.
Commit returns two copies of the same ID—`items` shows `inserted` for index 0 and
`confirmed_existing` for index 1—and rejected index 2. Report one distinct record plus partial
preservation due to rejection. Never claim that all three items were saved or try to encode the rejected secret.

## Readback discrepancy

A saved procedure is missing an essential non-secret parameter from available context. Report the
omission and submit a complete corrected procedure in a new flush with a fresh key and the known
`supersedes_id`. If the corrective flush still has a discrepancy, report it
and stop rather than repeatedly correcting or deleting assertions.

## Agent instructions are not knowledge

The client runs with a rule file that says to write to the vault only on an explicit request and
to report progress in a fixed format. The conversation is about a router. Extract only the router
knowledge. Do not store "the user wants writes only on an explicit request" or the progress-report
rule as preferences: the user did not say them in this conversation.

## Restated fact with an incidental addition

The conversation says a deployment was upgraded "this morning"; the check returns a stored
assertion with the same upgrade and date at similarity 0.96. Treat it as already stored. Do not
submit "the upgrade occurred in the morning" as a new assertion.

## Negative cases

The following must not call begin/append/commit:

- `Search my MCP for keyboard preferences.`
- `Forget assertion 123.`
- `Explain how MCP tools work.`
- `Summarize this chat.`
- `Remember this` when the user has not asked to use their MCP and another memory mechanism is in
  scope.
# Temporal backfill and reimport

User explicitly requests a flush of two available original source observations: January 31
confirms migration to MikroTik, January 1 observed Router X. Both have known source references.
Expected: candidate-check their texts, then preserve both observations with the same exact
property identity and different stable evidence keys, original observed dates/precision and
snapshot/change modes. Do not use supersedes_id or March import time as chronology. The January
1 backfill must still be submitted if its text matches: its observation is new evidence.

Reimporting the same sources reuses identical keys and payloads. Evidence receipts say replayed;
do not create new source keys merely for a new flush. A later proposal never changes confirmed
state. A later historical recollection uses historical mode. Missing source dates remain null;
missing identity remains unstructured. Explicitly corrected evidence read in the session uses
corrects_key rather than a configuration-change event. Report ambiguity/conflict as unresolved.
Commit/readback rules remain unchanged; current/history questions use resolve_knowledge_state.

# 0.5.0 maintenance behavior

- A whole procedure includes its appliance/context, preparation, ordered actions and caveats in a
  usable record; necessary parts get ordered links after commit. Missing details stay unknown.
- A statement copied from earlier advice uses reported/inferred evidence as appropriate, never
  verified merely because confidence defaults to 1.0. Dates come from evidence, not creation time.
- Reconciliation/readback calls use purpose=maintenance; a copied or fetched record alone does not
  trigger report_knowledge_usage. Report actual contribution explicitly only when appropriate.
- A request to repair a date fetches the revision, previews metadata changes and applies with the
  same expected revision; a stale revision causes reread/review. Changed meaning uses supersession.
- A resolved question links to its actual answer. A proposed plan is not marked completed solely
  because it was saved. Similar statements are candidates for review, never automatic deletion.
