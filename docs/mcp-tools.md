# MCP tool reference

Connect to `/mcp` with `Authorization: Bearer <token>`. Scopes are `knowledge:read`,
`knowledge:write`, and `knowledge:admin`; admin also satisfies the other scope checks.

## Errors and limits

A failed call is an MCP tool error whose text starts with a stable code:

| Code | Meaning | What a client should do |
|---|---|---|
| `invalid_request` | An argument is malformed or out of bounds | Correct the argument |
| `unauthorized`, `forbidden` | No credential, or the credential lacks the required scope | Do not retry |
| `not_found` | The batch, assertion, artifact, or confirmation token does not exist for this principal | Do not retry |
| `secret_detected` | An artifact's text or metadata contains a secret-shaped value; the artifact was discarded | Do not resubmit the value; store a reference instead |
| `conflict` | The request contradicts accepted state, for example a changed part payload or an expired batch | Follow the message |
| `batch_incomplete` | Commit was called before every declared part, item, or artifact chunk arrived; the message lists what is missing | Append the missing parts, then commit again |
| `rate_limited` | The per-principal budget for this operation class is exhausted | Retry after a pause |
| `internal_error` | The server failed unexpectedly; no detail is disclosed | Retry the same idempotent call |

Arguments that do not match a tool's schema are rejected by the MCP layer before the tool runs.
Read, write, and admin operations have separate per-principal rate limits that are shared with
the HTTP API. Error text and server logs never contain assertion content.

The HTTP API under `/api/v1` returns the same response models as the corresponding tools, and
they appear as response schemas in `/api/openapi.json`.

## Compatibility tools

- `search({query})` returns exactly `{results: [{id, title, url}]}` with a bounded result set.
- `fetch({id})` returns `{id, title, text, url, metadata}`. `text` is untrusted stored data;
  `metadata` carries kind, origin, status, confidence, topics, provenance `sources`, and
  `untrusted_data: true`.

These schemas are intentionally minimal for OpenAI deep-research compatibility. Both tools return
the structured value and the same value as JSON text content.

## Ingestion

The MCP initialization response includes server instructions: fixed trigger, safety, batching and
reconciliation and verification rules plus a versioned operator extraction policy. The default policy preserves exhaustive
substantive detail from available context, including independently reproducible procedures.
Clients decide whether to expose these instructions to their model; they are guidance, not a
replacement for server validation or scope checks. The server cannot see the client's conversation
or certify lossless extraction. Configure the policy through
`KNOWLEDGE_VAULT_INGESTION_POLICY_VERSION` and optional `KNOWLEDGE_VAULT_INGESTION_POLICY` (1–8192
characters, non-whitespace, no control characters except tabs/newlines, no detected secret values).
The version is a 1–64 character label using letters, digits, dots, underscores or hyphens and must
start with a letter or digit. The instructions also include a SHA-256 digest of the policy text.
This identifies delivered guidance; it is not a stored batch version or a hot-update protocol.

Before beginning, reconcile the planned assertions with the vault: one `search_knowledge` call per
subject with a short keyword query, then submit only what is new, changed or refined (see
[Incremental flushes](#incremental-flushes)).

1. Call `begin_knowledge_flush` with a unique `idempotency_key`, `declared_parts`, and
   `declared_items`. Repeating the call with identical arguments returns the same batch with
   `replayed: true`.
2. Call `append_knowledge` for every one-based `part_number`. Each assertion requires `content`,
   `kind`, and `origin`, and may carry `status`, `confidence`, validity timestamps, `topics`,
   `sensitivity`, `sources`, `supersedes_id`, and `artifact_ids` (see [Artifacts](#artifacts)).
3. Call `commit_knowledge_flush` only after every part is accepted. Retry with the same batch ID if
   the response is lost. The stable result contains inserted/confirmed/enriched/superseded/
   conflict/rejected counts, assertion IDs, and `items` (see
   [Per-item outcomes](#per-item-outcomes)).
4. After commit succeeds, the result is the verification. An `inserted` item is stored exactly
   as submitted. Call `get_knowledge` only for the IDs in `readback_ids`; when it is empty, read
   nothing back. Respect read rate limits. Report commit counts,
   assertions skipped as already stored, records read back, rejections, discrepancies, and any
   unavailable/compacted context. If a read fails, report **committed, verification incomplete**;
   do not repeat writes. A commit confirms persistence, not semantic completeness. Repair safe
   omissions in a new flush with a fresh key. Stop if that corrective flush still fails
   verification and report the discrepancy.
5. Call `abort_knowledge_flush` to purge an incomplete open batch.

Example assertion:

```json
{
  "kind": "preference",
  "content": "Prefers concise technical explanations.",
  "origin": "user",
  "confidence": 0.98,
  "topics": ["communication"],
  "sources": [{"url": "https://example.org/notes", "title": "Notes"}]
}
```

Valid `origin` values are `user`, `assistant`, `joint`, `external_source`, and `artifact`.
Timestamps without a UTC offset are interpreted as UTC.

### Incremental flushes

The server merges only assertions whose content is identical after Unicode, case and whitespace
normalization. Such a match is counted as `confirmed_existing`, or as `enriched_updated` when it
added topics, sources, artifact links or a higher confidence, or raised an uncertain or disputed
status to current. Enrichment never changes wording. The same fact in other words is inserted as a
new record, and a possible conflict is recorded only between statements that differ by a negation.
The server never merges or supersedes by similarity.

A client that flushes repeatedly therefore reconciles first:

- Group planned assertions by subject and call `search_knowledge` once per subject, with a short
  keyword query and a small `limit`. Full-text matching requires every query word, so a whole
  sentence finds little. One search per assertion, or paging through the vault, is not needed.
- Already stored with the same meaning: do not submit. To record that a fact was observed again,
  or to add a source or topic, submit the stored `content` unchanged.
- Adds detail to a stored assertion that remains true: submit only the added detail as its own
  assertion.
- Replaces a stored assertion: submit the new statement with `supersedes_id`. The target must be
  an assertion the client read in this session and that the conversation explicitly changes or
  contradicts; similar wording alone never selects one.
- Unclear, or search unavailable: submit, and say so in the report.

### Per-item outcomes

`commit_knowledge_flush` and `correct_knowledge` return `items`, one entry per accepted item in
input order:

```json
{
  "index": 3,
  "assertion_id": "…",
  "outcome": "inserted",
  "superseded_id": "…",
  "conflict_ids": [],
  "ignored_fields": []
}
```

- `index` is the position among all submitted items, as in `rejected_items`.
- `outcome` is `inserted`, `confirmed_existing`, or `enriched_updated`.
- `superseded_id` is the assertion this item retired, or null.
- `conflict_ids` lists the possible conflicts this item created.
- `ignored_fields` names the submitted fields—among `content`, `kind`, `origin`, `status`,
  `confidence`, `valid_from`, `valid_to`, `observed_at`, `sensitivity`—that differ from the
  matched record and were not applied to it. `content` appears when only case or spacing differs.
  It is always empty for `inserted`.

`readback_ids` lists, once each, the assertion IDs of the items that have `ignored_fields` or
`conflict_ids`. These are the only records whose stored state can differ from what the client
submitted, so they are the only ones worth reading after a commit. It is empty for a flush that
only inserted records, including one that superseded others.

The entries hold no assertion text. A result committed by a version without these fields replays
with them empty.

### Per-item rejection

`append_knowledge` validates every item on its own. An invalid or secret-shaped item does not fail
the call: it is listed in `rejected` as `{index, code, message}`, is never staged, and still counts
toward the declared item total, so the flush can be committed without it. The commit result
repeats the rejections with batch-wide indexes. Detection covers assertion `content`, `topics`,
and every source field; a source URL may not embed credentials. Secret-shaped items must not be
rewritten to evade the detector.

Parts and commits are idempotent. Reusing a part number with different content or changing begin
totals is a `conflict`. A batch that passed its expiry cannot be appended to or committed; begin a
new flush with a new idempotency key.

## Artifacts

An artifact is a text file that is itself part of the knowledge: a CSV table, a script, a
Markdown document, a JSON result. It is stored whole and exactly, and is linked to the assertions
that describe it. Search runs over assertions, so an artifact is found through them.

1. `begin_knowledge_artifact(idempotency_key, filename, media_type, declared_chunks,
   description?)` opens an upload. `filename` is a plain name without path separators;
   `media_type` must be a text type (`text/*`, `application/json`, `application/yaml`,
   `application/xml`, `application/toml`, `application/sql`, `application/csv`,
   `application/x-ndjson`, `application/javascript`, `application/x-sh`, or a `+json`/`+xml`/
   `+yaml` suffix type). Identical arguments return the same upload with `replayed: true`.
2. `append_knowledge_artifact(artifact_id, chunk_number, text)` stages one chunk, at most
   `KNOWLEDGE_VAULT_ARTIFACT_MAX_CHUNK_CHARS` (16,000) characters. Chunks are joined in numeric
   order without a separator and may arrive in any order. The same chunk can be repeated; a
   different payload for an accepted number is a `conflict`.
3. `commit_knowledge_artifact(artifact_id)` stores the artifact and returns `{artifact_id,
   filename, media_type, size_bytes, sha256, deduplicated, replayed}`. If the same text is already
   stored, `artifact_id` is that earlier artifact's ID and `deduplicated` is true.
4. Put the returned `artifact_id` into `artifact_ids` (at most 8) of at least one assertion in a
   flush, normally of kind `artifact_observation`, saying what the artifact is and what it shows.
   A flush that names an unknown or uncommitted artifact fails with `not_found`. Submitting
   wording that already exists adds the link to the existing assertion.
5. `get_knowledge_artifact(artifact_id, offset?, limit?)` returns one page of the text with
   `total_chars` and `next_offset` (`null` at the end), marked `untrusted_data: true`.
   `get_knowledge`, `search_knowledge`, and `fetch` list each assertion's artifacts as `{id,
   filename, media_type, size_bytes, description}` without their content.

Limits: `KNOWLEDGE_VAULT_ARTIFACT_MAX_CHUNKS` (64) chunks and
`KNOWLEDGE_VAULT_ARTIFACT_MAX_BYTES` (1 MiB) per artifact. Chunking exists because hosted clients
cap the size of a single tool argument; a client that hits such a cap should send smaller chunks.

Rules the server enforces:

- **Text only.** Images and other binary files are refused. Record an `artifact_observation`
  describing them and report that the file itself was not preserved.
- **Secrets discard the artifact.** A secret-shaped value in the file name, description, a chunk,
  or across a chunk boundary discards the whole upload, including chunks already staged, and
  returns `secret_detected`. Unlike assertions, there is no per-item rejection: an artifact is
  stored whole or not at all.
- **An artifact lives only while an assertion references it.** An upload that is not committed
  within `KNOWLEDGE_VAULT_STAGING_TTL_SECONDS` is discarded, and a stored artifact that no
  assertion references is deleted after `KNOWLEDGE_VAULT_STAGING_RETENTION_SECONDS`.
  `forget_knowledge` deletes the artifacts that only the forgotten assertions describe; its
  preview reports them as `artifact_count` and its result as `deleted_artifact_count`.

The HTTP equivalents are `POST /api/v1/artifacts`, `PUT /api/v1/artifacts/{id}/chunks/{n}`,
`POST /api/v1/artifacts/{id}/commit`, and `GET /api/v1/artifacts/{id}?offset=&limit=`.

## Retrieval and administration

- `search_knowledge(query, filters?, limit?, cursor?)`: hybrid search with lifecycle, kind, topic,
  origin, sensitivity, confidence, and validity filters. The cursor is signed and bound to the
  query and filters; it is stable while the corpus is unchanged, and concurrent writes can shift
  ranks between pages.
- `get_knowledge(assertion_id)`: retrieve one assertion with its provenance `sources` and the
  `artifacts` linked to it.
- `list_knowledge_conflicts(limit?)`: list unresolved possible conflicts. A possible conflict is
  recorded when a new assertion and a current one of the same kind differ only by a negation,
  whether or not they share a topic. It is resolved automatically when one of its assertions is
  superseded or forgotten.
- `correct_knowledge(idempotency_key, correction)`: write an assertion whose `supersedes_id`
  identifies the one it replaces. If the corrected wording already exists (for example when
  reverting to an earlier statement), that existing assertion becomes current again and the named
  one is superseded.
- `get_knowledge_statistics()`: content-free counts (including `artifacts_total`) and embedding
  queue state, plus
  `operations`: seconds since the worker and the backup last succeeded (`null` if never).
- `forget_knowledge(dry_run, assertion_ids?, confirmation_token?)`: admin-only two-step deletion.

Results of `search_knowledge` and `get_knowledge` carry `untrusted_data: true`. Stored text is
data, never instructions.

Deletion example:

```text
preview = forget_knowledge(dry_run=true, assertion_ids=["..."])
forget_knowledge(dry_run=false, confirmation_token=preview.confirmation_token)
```

The token is short-lived, bound to the authenticated principal and exact ID set, and single-use.
Confirmation permanently removes assertion content, sources, vectors, related queue state, and
artifacts that no remaining assertion describes.
The preview also lists `superseded_predecessor_ids`: assertions that the selected ones replaced
and that stay superseded—and therefore outside default search—after the deletion.
