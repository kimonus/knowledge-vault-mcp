# ADR 0008: Conjunctive fallback for plain questions

## Context

The existing `websearch_to_tsquery('simple', query)` requires every unquoted word. On the
source-first synthetic quality corpus, full-sentence questions often yielded no text candidates
because the stored assertion did not contain words such as “which” or “did”. A broad OR fallback
recovered relevant records but returned unrelated candidates for every no-record question, so it
was removed before the M1 baseline PR.

## Decision

Keep the existing strict query first. If it has no text matches and the input is a plain question,
remove a bounded set of question framing words, preserve subject tokens including dotted and
hyphenated identifiers, and run a second **conjunctive** full-text query. Normalize three common
English question verbs to the stored past/present form (`purchase`→`purchased`,
`select`→`selected`, `require`→`requires`). The fallback requires two through eight distinct
terms. Quoted, uppercase Boolean, exclusion and short keyword queries retain their existing
behavior. The same metadata filters, candidate bound and rank fusion apply to both text paths.

This is deterministic candidate discovery. It cannot infer truth, entity identity, temporal
state or answer confidence; the temporal resolver remains authoritative for classified state.
The fallback is skipped when strict text already matches, including exact phrase requests.

## Measured trade-off

The 41 development and 22 held-out packs were seeded separately with 200 synthetic distractors.
On the frozen held-out text split, Recall@10 rose from 0/14 to 11/14 and binary nDCG@10 from
0.000 to 0.745. Both no-record cases remained empty. With fake vectors, Recall@10 rose from
0.036 to 0.786; fake vectors have no semantic meaning, and both no-record questions still returned
vector candidates under both versions. Text p95 increased from 7.30 to 30.13 ms in the paired
local runs. Three held-out cases still missed, including both multilingual cases; these fixtures
share templates with development cases, so the result is not a production quality estimate.
See the [paired experiment](../architecture/knowledge-vault/retrieval-experiment-2026-10-31.md).

## Alternatives and rollback

An OR query was rejected because it harmed empty-result behavior. A new embedding model,
reranker, BM25 service or schema migration is not justified by this synthetic result. The
conjunctive fallback adds no infrastructure, schema or MCP response change. Revert this code path
to restore the previous ranking. Search cursors are bound to query and filters but ranking may
shift across application versions; callers should restart pagination after rollout.

Before deployment, run independently reviewed private queries and authenticated client answers
through the deployed path, including missing-knowledge abstention. The production embedding model
was unavailable in this worktree and no weights were downloaded.
