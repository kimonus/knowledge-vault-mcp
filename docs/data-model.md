# Data model

`assertions` is the authoritative record. Content is normalized, length-bounded, typed, and linked
to zero or more topics and source rows. Lifecycle state distinguishes current, superseded, and
other non-current assertions without overwriting history. A correction points explicitly to the
assertion it supersedes.

| Relation | Purpose and important invariants |
|---|---|
| `assertions` | UUID, kind, content, origin, confidence, validity dates, topics, lifecycle status, supersession link, text-search vector, optional 384-dimensional embedding; database checks enforce enum/range/date rules |
| `assertion_sources` | URL-based provenance for one assertion, with title, publisher, and retrieval time; cascades on hard deletion |
| `flush_batches` | Principal-scoped idempotency key, declared totals, lifecycle state, stable commit result, timestamps |
| `flush_parts` | One hash and temporary validated payload per batch part number; mismatched replay is rejected and payloads are deleted after commit or abort |
| `embedding_jobs` | Persistent pending/claimed/retry/dead queue with attempts and next-run time |
| `knowledge_conflicts` | Bounded review records connecting potentially incompatible current assertions |
| `confirmation_tokens` | Short-lived, one-use digest and target ID set for hard deletion |
| `deletion_audit` | Content-free principal, action, deletion count, correlation ID, and time; never retains deleted assertion text |

PostgreSQL owns referential integrity and uniqueness. The migration enables `vector`, creates the
tables and indexes, and maintains the generated full-text search value. Exact normalized-content
deduplication precedes semantic conflict hints. Flush payload text exists only while a batch is
open; commit atomically materializes assertions and removes the part payloads, and the worker
expires batches that pass `KNOWLEDGE_VAULT_STAGING_TTL_SECONDS` and deletes their payloads. Batch
metadata and used or expired confirmation tokens are purged after the retention period.

A vector is replaced only after successful model inference. Each vector records the model that
produced it, and search compares the query only against vectors of the configured model, so a
rebuild for a different model falls back to text search for assertions it has not reached yet
instead of mixing vector spaces.

Revisions live in [migrations/versions](../migrations/versions/): `0001_initial` creates the
schema and `0002_enumerated_value_checks` adds CHECK constraints for every enumerated column.
Each revision spells out its DDL and never derives it from the ORM models. Application tables are
defined in [tables.py](../src/knowledge_vault/persistence/tables.py).

There is no approximate vector index: similarity search scans the embeddings of the filtered
rows, which is adequate for a personal corpus of tens of thousands of assertions.
