# Golden flush cases

These cases specify observable decisions and tool-call order. Values such as UUIDs and idempotency
keys are illustrative.

## Exact trigger

User: `flush knowledge to my MCP`

Expected: extract every meaningful assertion in available context, then call:

```text
begin_knowledge_flush(key=K, declared_parts=N, declared_items=M)
append_knowledge(batch_id=B, part_number=1, assertions=[...])
...
append_knowledge(batch_id=B, part_number=N, assertions=[...])
commit_knowledge_flush(batch_id=B)
get_knowledge(assertion_id=each distinct returned ID)
```

Compare readback with every planned assertion, then report server commit counts, records verified,
rejections and omissions. Disclose context compaction if known.

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
recipe with "make bread using a long fermentation". Fetch every committed ID and compare quantities,
units, temperatures, timing, ordering and the rejected alternative with the extraction checklist.

## Readback failure after commit

Commit succeeds with two IDs. The first read succeeds; the second returns a transient error.
Retry the second read, respecting rate limits. Do not repeat begin/append or create another flush
because of the read failure. If verification cannot finish, report "committed, verification
incomplete", one record verified, and the unchecked ID. Do not claim that nothing was saved.

## Duplicate IDs and rejected items

Three submitted items include two exact normalized duplicates and one secret-shaped rejection.
Commit returns two copies of the same ID and rejected index 2. Read the ID once, compare it against
both accepted checklist entries, and report one distinct verified record plus partial preservation
due to rejection. Never claim that all three items were saved or try to encode the rejected secret.

## Readback discrepancy

A saved procedure is missing an essential non-secret parameter from available context. Report the
omission and submit a complete corrected procedure in a new flush with a fresh key and the known
`supersedes_id`. Fetch its returned ID. If the corrective flush still has a discrepancy, report it
and stop rather than repeatedly correcting or deleting assertions.

## Negative cases

The following must not call begin/append/commit:

- `Search my MCP for keyboard preferences.`
- `Forget assertion 123.`
- `Explain how MCP tools work.`
- `Summarize this chat.`
- `Remember this` when the user has not asked to use their MCP and another memory mechanism is in
  scope.
