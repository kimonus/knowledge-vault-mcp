# Next-agent handoff — Knowledge Vault multilingual retrieval

**State:** bounded post-roadmap investigation. T0/M1/M2/M2.1 remain closed; M3–M5 deferred. Do not start another open-ended roadmap.

## Evidence from 2026-11-07

A user-authorized Knowledge Vault flush inserted 16/16 atomic assertions with zero rejects/conflicts. All 3,152 embeddings subsequently completed. The `search_knowledge` maintenance-purpose eight-query smoke test after indexing returned relevant or partially relevant results in top five for six queries, but this is neither a representative accuracy estimate nor a root-cause diagnosis.

| Query | Observed top-five result |
| --- | --- |
| `Knowledge Vault roadmap formally closed PR 97 2026` | Closure record rank 1 |
| `M3 M4 M5 deferred source coverage curation feedback` | M4 rank 2, M5 rank 5; combined answer incomplete |
| `PR 96 synthetic lifecycle importlib CI` | CI-fix record rank 2 |
| `M2.1 18/18 Codex client answers` | M2.1 record rank 1 |
| `Какие этапы Knowledge Vault roadmap завершены, а какие отложены?` | Closure record rank 5; combined answer incomplete |
| `Почему не стали реализовывать серверные receipts для source coverage?` | M3 rationale rank 1 |
| `Что было исправлено в CI PR #96?` | CI-fix record absent top five; unrelated records |
| `Какие ограничения были у оценки качества M2.1?` | M2.1 limitation record absent top five |

`embedding_degraded=false`. The queries are not controlled translation pairs; differences in phrasing and corpus composition are confounders. Scores are ranking/fusion values, not calibrated probabilities. Retrieval of stored assertions does **not** validate completeness of extraction from the original conversation. No production private data or traces should be published.

## Agent task — proceed autonomously until a real authorization boundary

1. Inspect current product main, roadmap, M2/M2.1 evidence, search pipeline, query embedding and lexical fallback. Verify deployed image identity separately; do not assume current main equals production.
2. Construct disposable synthetic assertion corpus with relevant records and unrelated distractors; preregister equivalent Russian/English queries, negative/no-record controls and expected gold IDs before testing. Include analogues of the eight observations.
3. Run real MCP retrieval in isolated environment, record top-5/top-10 candidate recall and rankings by lexical-only, vector-only, hybrid/fusion; examine normalization, tokenization, embedding, candidate caps and fallback. Identify where the intended record is lost.
4. If defect reproducible, implement smallest reversible change with tests for bilingual parity, false positives/no-record, filters, pagination, authorization and latency. Run full CI; independently review before merge. If not reproducible, stop with a bounded evidence report. Do not expand scope.
5. Report verified root cause (or uncertainty), exact reproducible cases, before/after metrics on same frozen cases, CI, regression risk and rollback. Product code changes only if justified; no production deployment or production writes without explicit authorization.

## Exit criteria

A reviewed, sanitized report plus either a tested CI-green fix or an explicit no-fix decision. Do not turn synthetic results into a claim of field accuracy. Keep optional independent-gold coordination PR #50 outside this gate.

## References

- [Roadmap](../architecture/knowledge-vault/roadmap.md)
- [PR #96](https://github.com/kimonus/knowledge-vault-mcp/pull/96)
- [PR #97](https://github.com/kimonus/knowledge-vault-mcp/pull/97)
- [Closure decision PR #51](https://github.com/kimonus/knowledge-vault-coordination/pull/51)
