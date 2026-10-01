# MCP tool reference

Connect to `/mcp` with `Authorization: Bearer <token>`. Scopes are `knowledge:read`,
`knowledge:write`, and `knowledge:admin`; admin also satisfies the other scope checks. Tool errors
are returned as MCP tool errors without assertion text in logs.

## Compatibility tools

- `search({query})` returns exactly `{results: [{id, title, url}]}` with a bounded result set.
- `fetch({id})` returns `{id, title, text, url, metadata}`. `text` is untrusted stored data.

These schemas are intentionally minimal for OpenAI deep-research compatibility.

## Ingestion

1. Call `begin_knowledge_flush` with a unique `idempotency_key`, `declared_parts`, and
   `declared_items`.
2. Call `append_knowledge` for every one-based `part_number`. Each assertion includes `kind`,
   `content`, and optional `origin`, `confidence`, validity dates, topics, and source fields.
3. Call `commit_knowledge_flush` only after every part is accepted. Retry with the same batch ID if
   the response is lost. The stable result contains accepted/deduplicated/rejected counts and IDs.
4. Call `abort_knowledge_flush` to purge an incomplete open batch.

Example assertion:

```json
{
  "kind": "preference",
  "content": "Prefers concise technical explanations.",
  "origin": "explicit",
  "confidence": 0.98,
  "topics": ["communication"]
}
```

Parts and commits are idempotent. Reusing a part number with different content or changing begin
totals is an error. Secret-shaped items are rejected individually and must not be rewritten to
evade the detector.

## Retrieval and administration

- `search_knowledge(query, filters?, limit?, cursor?)`: hybrid search with lifecycle, kind, topic,
  origin, confidence, and validity filters; returns a stable next cursor.
- `get_knowledge(assertion_id)`: retrieve one assertion UUID.
- `list_knowledge_conflicts(limit?)`: list unresolved possible conflicts without auto-resolving.
- `correct_knowledge(idempotency_key, correction)`: write a new assertion whose
  `supersedes_assertion_id` identifies the old one.
- `get_knowledge_statistics()`: content-free counts and embedding queue state.
- `forget_knowledge(dry_run, assertion_ids?, confirmation_token?)`: admin-only two-step deletion.

Deletion example:

```text
preview = forget_knowledge(dry_run=true, assertion_ids=["..."])
forget_knowledge(dry_run=false, confirmation_token=preview.confirmation_token)
```

The token is short-lived, bound to the authenticated principal and exact ID set, and single-use.
Confirmation permanently removes assertion content, sources, vectors, and related queue state.
