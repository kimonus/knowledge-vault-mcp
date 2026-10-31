# Knowledge maintenance and usage accounting

Status: accepted for implementation in 0.5.0.

Keep text assertions and explicit supersession. Add independent evidence, workflow and temporal
metadata, typed non-web provenance, named contexts, bounded assertion relationships, and topic
aliases. Existing records keep unknown evidence and unspecified temporal role. Existing confidence
is a legacy numeric score, not verification. Never infer dates, completion or corroboration.

Metadata correction is a scoped, idempotent write with optimistic revision checking. Assertion
text is immutable through that operation. Revisions change for logical metadata and legacy claim
updates, not embeddings or retrieval counters. Audit retains field names and actor/key hashes,
not previous assertion values. Deleting an assertion cascades its maintenance audit.

Store aggregate usage separately. Normal search counts returned IDs once; a subsequent fetch is
another retrieval and one explicit fetch. Maintenance, candidate checks and linked inspection do
not count. Counters describe successful service retrieval, not delivery or actual use. Actual use
requires an explicit writer report. Tracking failure is observable but does not fail reads.
Logical event IDs deduplicate retries for 24 hours. No raw queries or permanent click stream.
Compatibility search/fetch keep fixed inputs and default to normal accounting.

Relationships are child-to-parent part_of, dependent-to-prerequisite depends_on,
evidence-to-supported-claim supports, symmetric equivalent_to, and question-to-answer resolves. part_of and
depends_on must be acyclic; ordered part positions are unique within a parent. Supersession keeps
its existing dedicated pointer. Links never authorize changing the linked assertions.

Topic aliases expand search filters and lexical queries without rewriting stored topics. Duplicate
and competing-state reports are advisory. Different historical contexts/times can explain different
values. No automatic merging, deletion, source fetching or confidence increase is introduced.

All changes are additive for old readers/writers. Schema rollback removes new metadata and is
destructive; prefer application rollback on the expanded schema. Back up and restore-test before
promotion. All examples and fixtures are synthetic.
