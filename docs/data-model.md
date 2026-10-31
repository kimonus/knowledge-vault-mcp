# Data model

`flush_batches.request_digest` is an optional keyed request HMAC for one-call correction retries.
It is null for generic flushes and old receipts. It permits exact payload checks after staging
purge without keeping content or a reversible content hash; the key stays outside PostgreSQL.

Migration 0006 adds immutable `temporal_evidence`: stable source key, assertion FK (cascade delete),
exact identity hash, canonical payload hash, validated assertion payload and receipt timestamp.
An update-refusal trigger prevents rewriting evidence. Indexes cover identity/receipt/key and
assertion lookup. Multiple observations can reference the same deduplicated text while retaining
independent dates/provenance. Current state is a deterministic read-time projection over evidence,
not a mutation of assertion status. See [temporal semantics](temporal-knowledge.md) and
[decision 0007](decisions/0007-temporal-evidence.md). Existing assertions remain unchanged.
`temporal_retirements` records only a retained evidence key and time, so deleting its correction
does not reactivate an invalidated claim. It retains no deleted content or hash and cascades
when that retained evidence is forgotten. The deletion preview includes both temporal counts.

0.5.0 adds evidence/temporal/workflow metadata, provenance, context/facet and a logical revision
to assertions. See [maintenance semantics](knowledge-maintenance.md) and
[decision 0006](decisions/0006-knowledge-maintenance.md). Migration 0005 is additive for old clients;
its downgrade discards new metadata. New relations are knowledge_contexts, assertion_links,
topic_aliases, assertion_usage and expiring maintenance_events. Assertion deletion cascades links,
usage and maintenance receipts. Logical revision has a database trigger covering old writers as
well; embeddings/access accounting do not bump it. No legacy evidence/date is inferred.

`assertions` is the authoritative record. Content is normalized, length-bounded, typed, and linked
to zero or more topics and source rows. Lifecycle state distinguishes current, superseded, and
other non-current assertions without overwriting history. A correction points explicitly to the
assertion it supersedes.

| Relation | Purpose and important invariants |
|---|---|
| `assertions` | UUID, kind, content, origin, confidence, validity dates, topics, lifecycle status, supersession link, text-search vector, optional 384-dimensional embedding; database checks enforce enum/range/date rules |
| `assertion_sources` | URL-based provenance for one assertion, with title, publisher, and retrieval time; cascades on hard deletion |
| `artifacts` | One text artifact: file name, media type, description, SHA-256, size, and the text itself; an upload is `open` until committed, then `stored`, or `duplicate` (pointing at the stored record with the same text). At most one stored record per content hash. `storage` is `text`; binary content would be a further kind |
| `artifact_chunks` | Numbered staging text of an open upload with one hash per chunk; deleted on commit, rejection, or expiry |
| `assertion_artifacts` | Links an assertion to the artifacts it describes; cascades when either side is deleted |
| `flush_batches` | Principal-scoped idempotency key, declared totals, lifecycle state, stable commit result, timestamps |
| `flush_parts` | One hash and temporary validated payload per batch part number; mismatched replay is rejected and payloads are deleted after commit or abort |
| `embedding_jobs` | Persistent pending/claimed/retry/dead queue with attempts and next-run time |
| `knowledge_conflicts` | Bounded review records connecting potentially incompatible current assertions |
| `confirmation_tokens` | Short-lived, one-use digest and target ID set for hard deletion |
| `operational_heartbeats` | When each background duty (worker maintenance, backup, watchdog) last succeeded; content-free |
| `deletion_audit` | Content-free principal, action, deletion count, correlation ID, and time; never retains deleted assertion text |

PostgreSQL owns referential integrity and uniqueness. The migration enables `vector`, creates the
tables and indexes, and maintains the generated full-text search value. Exact normalized-content
deduplication precedes semantic conflict hints. Flush payload text exists only while a batch is
open; commit atomically materializes assertions and removes the part payloads, and the worker
expires batches that pass `KNOWLEDGE_VAULT_STAGING_TTL_SECONDS` and deletes their payloads. Batch
metadata and used or expired confirmation tokens are purged after the retention period.

An artifact is retained only while an assertion references it. The worker deletes uploads that
were never committed and stored artifacts left unreferenced past the retention period, and hard
deletion of an assertion deletes the artifacts that no other assertion describes. Artifact text
is in the same database, so backups and restores cover it without a second store.

A vector is replaced only after successful model inference. Each vector records the model that
produced it, and search compares the query only against vectors of the configured model, so a
rebuild for a different model falls back to text search for assertions it has not reached yet
instead of mixing vector spaces.

Revisions live in [migrations/versions](../migrations/versions/): `0001_initial` creates the
schema, `0002_enumerated_value_checks` adds CHECK constraints for every enumerated column, and
`0003_operational_heartbeats` adds the heartbeat table, and `0004_text_artifacts` adds the
artifact tables.
Each revision spells out its DDL and never derives it from the ORM models. Application tables are
defined in [tables.py](../src/knowledge_vault/persistence/tables.py).

There is no approximate vector index: similarity search scans the embeddings of the filtered
rows, which is adequate for a personal corpus of tens of thousands of assertions.
