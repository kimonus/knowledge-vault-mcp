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

## Negative cases

The following must not call begin/append/commit:

- `Search my MCP for keyboard preferences.`
- `Forget assertion 123.`
- `Explain how MCP tools work.`
- `Summarize this chat.`
- `Remember this` when the user has not asked to use their MCP and another memory mechanism is in
  scope.
