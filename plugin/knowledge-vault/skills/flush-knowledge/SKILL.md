---
name: flush-knowledge
description: Flush durable knowledge to the private Knowledge Vault MCP when the user says "flush knowledge to my MCP" or a close natural variant asking to save, persist, or flush the current conversation's knowledge to their MCP. Do not activate for ordinary summaries, memory questions, retrieval, or vague mentions of MCP.
---

# Flush knowledge

Convert all meaningful knowledge in the currently available conversation context into atomic
assertions and durably commit it through the Knowledge Vault MCP. Follow the server's current
operator extraction policy when the client exposes its MCP instructions. Persistence is complete
only after `commit_knowledge_flush` succeeds; verified preservation also requires reading back
every distinct committed assertion ID and comparing it with the extraction checklist.

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
- Preserve exact names, versions, parameters, quantities, units, commands, code, ordering,
  prerequisites, expected outcomes, validation steps, caveats and context needed for reproducibility.
  Keep a recipe or procedure's necessary steps together so its record is usable alone. For a
  procedure above the schema's size limit, use self-contained subprocedures with prerequisites and
  disclose any detail that cannot be represented. Never invent missing steps or values.
- Store claims and concise provenance, not copied pages or transcript structure.
- Exclude filler, duplicate wording, hidden reasoning, and facts already expressed by a more precise
  assertion in the same flush.
- Never invent an assertion to make the flush look complete.
- Never submit credentials, passwords, access tokens, private keys, cookies, or secret values. A
  non-secret reference such as "credential is stored in Kubernetes Secret X" is allowed.

## Artifacts

When a text file produced or examined in the conversation is itself part of the knowledge—a CSV
table, a script, a Markdown document, a JSON result—store it whole before the flush:

1. `begin_knowledge_artifact` with a new idempotency key, a plain file name, a text media type and
   the exact number of chunks.
2. `append_knowledge_artifact` for every numbered chunk, within the advertised size. Copy the text
   exactly; never summarise, reformat or truncate inside an artifact.
3. `commit_knowledge_artifact`, then list the returned `artifact_id` in `artifact_ids` of at least
   one assertion that says what the artifact is and what it shows. An artifact that no assertion
   references is deleted.

Retry a failed call with identical arguments. Never upload a file that contains credentials: a
detected secret discards the whole artifact, and the detector does not know every secret shape.
Images and other binary files cannot be stored; record an `artifact_observation` describing them
and say in the report that the file itself was not preserved. Report stored, deduplicated,
rejected and unpreserved artifacts separately from assertion counts.

Use `supersedes_id` only when the conversation explicitly corrects an assertion whose server ID is
known. Do not infer a supersession target from semantic similarity.

Keep a checklist of the planned assertions in client context for later verification. If none are
useful, report that without beginning a flush. If extraction exceeds the server's batch limit, use
multiple complete flushes with separate keys; do not truncate or overdeclare one batch.

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
7. After commit succeeds, call `get_knowledge` for every distinct ID in `assertion_ids`. IDs follow
   accepted input order; `rejected_items` indexes cover all submitted items. Read duplicate IDs once,
   but compare each against all corresponding checklist entries. Check substantive content, metadata
   and provenance, accounting for documented normalization, deduplication, enrichment and explicit
   supersession. Also check whether the extraction itself missed useful information.
8. Honor rate limits, pause and retry transient read failures. A failed read does not undo commit:
   report "committed, verification incomplete" with unchecked IDs; do not repeat writes. Report
   mismatches explicitly. Repair safe omissions with a new flush and key; use a known
   `supersedes_id` for an explicit correction. If a corrective flush still fails verification, stop
   and report the unresolved discrepancy rather than looping.
9. Reproduce server commit counts: inserted, confirmed existing, enriched/updated, superseded,
   possible conflicts, rejected, and embedding pending. Report distinct records verified,
   rejections, omissions and context limits separately. Rejected items mean partial preservation;
   never describe them as fully saved.

Readback proves persistence, not lossless extraction of unseen or compacted conversation. If
context may have been compacted, say that only currently available context was flushed. If begin,
append, or commit ultimately fails, say the flush is incomplete and the chat should not yet be
deleted. Never describe an append-only state as committed.

For behavioral examples and retry invariants, read
[golden cases](references/golden-cases.md) when testing or diagnosing this skill.
