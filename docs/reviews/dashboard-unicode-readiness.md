# Dashboard Unicode readiness regression

This is a deployment-verification defect record, not an independent security review.
No production knowledge, credentials, record identifiers or payloads are included here.

## Finding

**Severity: high availability/functionality blocker for optional Dashboard enablement.**
`BrowseService.assertions()` selected `left(content, 400)` directly from stored text. On the
repository's pinned PostgreSQL 17.8 image, valid compressed Unicode text can trigger SQLSTATE
`22021`; a keyset scan then stops at the affected page. The BFF refuses the core failure as a
safe dependency error, without returning a partial page or leaking the exception. Ordinary
full-text retrieval is unaffected by this particular excerpt expression.

The attack prerequisite is a retained long Unicode assertion; malicious content is not needed.
There is no demonstrated authentication bypass, corruption, credential exposure or writer
authority. The optional UI must remain disabled when its complete-traversal acceptance gate fails.

## Reproduction and evidence

`test_toasted_unicode_excerpts_preserve_http_keyset_traversal` stores synthetic compressed text
between two ordinary assertions in a disposable digest-pinned pgvector PostgreSQL. It exercises
the real authenticated inspection HTTP adapter, one-row keyset pages and both exhaustive and
lexical browsing. Four cases cover ellipsis, CJK, Cyrillic and supplementary-plane characters.
Compression is verified with `pg_column_size` and `octet_length`.

Before the change, three cases fail with HTTP 500 / SQLSTATE `22021`; the Cyrillic control passes.
No live database is used by this regression test. Read-only diagnosis separately established that
complete text remained valid and materializing before truncation produced the exact Unicode
prefix. The deployed optional Dashboard was disabled rather than accepted with an incomplete scan.

After remediation, all four regression cases pass. Local validation passes 579 core
unit/property/contract/integration/e2e tests and 131 offline quality tests, with 98.03% statement
and 93.05% branch coverage for application code. Ruff, Pyright, lock consistency and Helm lint
also pass. Required CI and release rehearsal remain separate exact-head gates.

This matches [PostgreSQL bug #19406](https://github.com/postgres/postgres/commit/5d5232bc38d36a30b569396f15e5a22cd6ee529b),
fixed in [PostgreSQL 17.9](https://www.postgresql.org/docs/17/release-17-9.html). The regression
incorrectly validates an incomplete character beyond the requested substring in a TOAST slice.

## Remediation

Select `left(content || '', 400)` so PostgreSQL materializes the text before taking its excerpt.
Only the excerpt query changes. Full retained text stays inside PostgreSQL for this operation;
the application receives the same bounded summaries, page size, keyset order, cursor binding
and response schema. No database migration, stored-text rewrite, frontend change or new authority
is introduced. The regression checks exact 400-character prefixes, truncation flags and traversal
past the affected record.

Production acceptance still requires the normal reviewed release/signature gates and a successful
authenticated complete scan through the deployed BFF after installing the corrected core image.
This source change does not itself certify a deployment or change database patch policy.
