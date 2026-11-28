# ADR 0011: Preserve persisted Unicode normalization and content hashes

- Status: Accepted for TASK-0018 verification compatibility resolution
- Date: 2026-11-28

The original brief requires deterministic normalization, stable hashes, exact deduplication and
idempotent receipts. The established content algorithm is NFKC, collapsed/trimmed whitespace,
then Unicode casefold. `normalized_content` and its SHA-256 are persisted and used for exact
candidate matching, ingestion deduplication and correction/temporal preconditions.

The property test incorrectly also required the final casefolded string to be NFKC. For U+1E96,
casefold produces U+0068 U+0331. That output is stable under the established algorithm but is not
NFKC. U+01F0 and U+0390 give further reproducible decompositions. This was a test-contract defect,
not evidence of a changed normalization algorithm or damaged persisted data.

Preserve the existing runtime algorithm and all stored hashes. Correct the property to check
idempotence, compatibility-equivalent input, stable casefold and whitespace. Lock decomposing
Unicode examples to their exact legacy bytes and hashes, and seed a real disposable PostgreSQL
row with the legacy hash to verify candidate matching, deduplication and exact commit replay.
The normalizer remains byte-identical to application base
`698811896dd294ed321fca78c905b3ad14b7d773`.

Do not add a post-casefold NFKC pass: that would change existing hashes, could insert duplicate
assertions beside legacy rows and would require a separately designed data migration/collision
policy. No schema change, rehash, new normalization version or MCP contract change is introduced.
TASK-0018's earlier failed gate remains historical evidence in r1; r2 records this explicit
resolution and the new execution results. Independent review acceptance remains pending.
