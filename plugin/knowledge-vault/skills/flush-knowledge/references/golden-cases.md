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
```

The response reports only server-provided commit counts and discloses context compaction if known.

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

## Negative cases

The following must not call begin/append/commit:

- `Search my MCP for keyboard preferences.`
- `Forget assertion 123.`
- `Explain how MCP tools work.`
- `Summarize this chat.`
- `Remember this` when the user has not asked to use their MCP and another memory mechanism is in
  scope.
