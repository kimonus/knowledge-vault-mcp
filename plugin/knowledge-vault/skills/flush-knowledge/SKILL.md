---
name: flush-knowledge
description: Flush durable knowledge to the private Knowledge Vault MCP when the user says "flush knowledge to my MCP" or a close natural variant asking to save, persist, or flush the current conversation's knowledge to their MCP. Do not activate for ordinary summaries, memory questions, retrieval, or vague mentions of MCP.
---

# Flush knowledge

Convert all meaningful knowledge in the currently available conversation context into atomic
assertions and durably commit it through the Knowledge Vault MCP. The flush is complete only after
`commit_knowledge_flush` succeeds.

## Extract

Read the entire context currently available to you. Extract each independently useful assertion,
including user-shared information, external findings, conclusions, decisions, considered and
rejected options, procedures, configurations, plans, uncertainties, open questions, and useful
artifact observations.

For every assertion:

- Preserve its epistemic character with the most accurate `kind`, `origin`, `status`, `confidence`,
  dates, topics, sensitivity, and source URLs.
- Keep it atomic and self-contained. Split compound statements when either part could change or be
  retrieved independently.
- Store claims and concise provenance, not copied pages or transcript structure.
- Exclude filler, duplicate wording, hidden reasoning, and facts already expressed by a more precise
  assertion in the same flush.
- Never invent an assertion to make the flush look complete.
- Never submit credentials, passwords, access tokens, private keys, cookies, or secret values. A
  non-secret reference such as "credential is stored in Kubernetes Secret X" is allowed.

Use `supersedes_id` only when the conversation explicitly corrects an assertion whose server ID is
known. Do not infer a supersession target from semantic similarity.

## Commit protocol

1. Choose a new, opaque idempotency key for this logical flush. Keep it unchanged across retries.
2. Count assertions, split them into parts within the tool's advertised bound, and call
   `begin_knowledge_flush` with exact totals.
3. Call `append_knowledge` for each numbered part. Number parts from 1 through the declared total.
4. On a transient failure, retry the same call with the same batch ID, part number, payload, and
   idempotency key. Never change an accepted part payload.
5. If an item is rejected, do not silently replace or omit it. Remove secret material if a safe
   reference retains the knowledge; otherwise leave it rejected and include the server count in the
   result.
6. Call `commit_knowledge_flush` only after every declared part has been accepted. Retry commit
   idempotently after a transient failure.
7. Report success only from the commit response and reproduce its counts: inserted, confirmed
   existing, enriched/updated, superseded, possible conflicts, rejected, and embedding pending.

If context may have been compacted, say that only currently available context was flushed. If begin,
append, or commit ultimately fails, say the flush is incomplete and the chat should not yet be
deleted. Never describe an append-only state as committed.

For behavioral examples and retry invariants, read
[golden cases](references/golden-cases.md) when testing or diagnosing this skill.
