# M2 — Measured retrieval improvement

**Priority:** high if M1 demonstrates retrieval failures. **Effort:** M (4–10 engineer days), medium confidence. **Dependencies:** T0 and a frozen M1 held-out set. **Owner:** search/ranking agent, separate from M3.

## Problem and evidence

Current search constructs `websearch_to_tsquery('simple', query)`, takes at most 250 text and 250 vector candidates, then fuses ranks at 0.55/0.45 without a relevance threshold ([search](../../../../src/knowledge_vault/services/search.py), [ranking](../../../../src/knowledge_vault/services/ranking.py)). Four read-only diagnostics yielded vector-only top score sequences; the relevance impact is unmeasured. A result page can contain semantically remote hits even when the system operates correctly.

## Scope and architectural decision

Run controlled ablations on M1 cases: current baseline, lexical term disjunction/coverage, phrase handling, candidate depth, fusion weights and calibrated threshold/empty-result behavior. Preserve model, data and held-out labels during comparison. Implement the smallest option that improves paired quality under p95 latency and memory limits. If lexical changes do not help, examine query construction and only then test alternative local embeddings or lightweight reranking. Do not introduce a new search service or ANN index for the present corpus size.

## Implementation requirements

Touch `services/search.py`, `services/ranking.py` and focused tests; use `domain/responses.py` if adding optional candidate diagnostics. Keep `search`/`fetch` compatibility schemas and bounded pagination. Define query intent handling for exact IDs, natural language, multilingual aliases and current/historical routing. Generic search must remain discovery; temporal resolver remains authoritative for classified state. Expose only content-free diagnostic counters (candidate counts, overlap, degraded mode, latency) with fixed labels; never log query or assertion text. Relevance cutoffs must be calibrated on dev set, not raw RRF score interpreted as probability. Document behavior when embeddings are pending or unavailable.

## Acceptance and testing

Report paired Recall@10, nDCG@10, empty-result accuracy and p95 steady-state latency by category/language; require a justified held-out gain without >2-point overall or >5-point protected-slice regression (revisit threshold after M1 variance). Test exact lexical hits, phrase paraphrases, cross-language queries, distractors, stale vectors, pagination after corpus changes, authorization filters, deletion and degraded text-only fallback. Run repository code gates. A search query that has no adequate evidence should return empty or clearly low-confidence candidates; evaluate false abstentions too.

## Migration, compatibility, risk and rollback

No schema migration for the recommended first experiment. A new model would require a separate reviewed rebuild and resource plan. Ranking changes may shift pagination and client citations; version or feature-flag the scoring mode, compare old/new offline, and revert code/config on regression. Keep old vectors intact during any later model experiment.
