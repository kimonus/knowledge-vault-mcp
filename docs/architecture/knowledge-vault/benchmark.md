# Document E — Knowledge quality benchmark specification

## Purpose and independent ground truth

The benchmark must answer four different questions: **Was useful knowledge extracted? Was the right stored record retrieved? Was temporal state resolved correctly? Did the assistant answer from evidence?** A vault search result is never its own truth label.

Create 60–100 synthetic, realistic source packs and 20–30 separately consented, access-controlled real cases if feasible. Each synthetic pack contains original dated statements, corrections, documents, and explicit operator-authored truth cards prepared **before** ingestion. Two reviewers independently annotate (a) atomic claims and required rationale/detail, (b) source reference, origin and evidence state, (c) entity/scope, (d) valid/observation/known time with precision, (e) expected current and historical state, and (f) answer with permissible citations or required abstention. Adjudicate disagreements and retain both raw labels and the decision record. Reviewers must read original source material, not system output. Keep real source material and IDs outside Git in an encrypted operator-local fixture store; commit only synthetic fixtures and redacted aggregate results. Split train/dev/held-out by **source scenario**, not by query, to prevent paraphrase leakage. Freeze the held-out set before tuning.

The ingestion fixture has two modes: (1) pre-seeded reviewed gold assertions to isolate retrieval/answer quality; (2) raw source presented to the client extraction workflow to measure extraction. Never score a search system against only the assertions its own extractor happened to store. Run temporal tests against a disposable migrated database; do not touch live knowledge.

## Coverage matrix and sample scenarios

| Category | Synthetic example and expected behavior |
|---|---|
| Current and historical configuration | A home router changed from vendor A to B; a later conversation recounts A. Current query returns B, past query returns A or uncertainty when dates lack precision. |
| Decision rationale | A battery choice rejects option X due to noise and cost; answer retains both decision and reasons with citations. |
| Considered vs purchased | A device is proposed, compared, then a different one is purchased. Never present proposal as completed purchase. |
| Troubleshooting | A failed fix precedes a successful procedure; answer distinguishes tried, failed, and working steps. |
| Corrections | User corrects a model number or reverses an earlier claim; corrected evidence wins only per explicit temporal semantics. |
| Multi-conversation integration | Several source packs mention one project under alias and translated name; retrieve all relevant claims without conflating scopes. |
| Multilingual | Equivalent English, Polish, Russian queries against the same gold records; compare recall without assuming exact parity. |
| Missing/contradictory | No source supports the asked fact, or two equal-quality sources disagree; abstain or report conflict. |
| Freshness | Old volatile price and durable preference coexist; answer marks price stale and retains preference. |
| Injection/deletion | Source includes instructions addressed to assistant; ignore them. Hard delete a gold assertion and verify no indexed path returns it. |

Include negative cases with common lexical overlap but wrong subject, a long procedure whose prerequisites are required, duplicate paraphrases, undated state, mixed observation/valid time, and a 200+ irrelevant-assertion distractor corpus. Metamorphic checks: import order permutation gives equivalent temporal result; language changes preserve relevant ID coverage within an agreed tolerance; adding distractors does not evict required top-10 records; proposal never becomes completed; deleting a claim removes it from lexical/vector/candidate/fetch paths; changing only transaction order does not change real-world state.

## KPIs and diagnostics

| Layer | Primary KPI | Supplementary diagnostics |
|---|---|---|
| Extraction | **Claim coverage:** gold required claims correctly captured with necessary detail and epistemic label, denominator includes missed claims | Unsupported claim rate, rationale preservation, source citation coverage, secret rejection, duplicate decision precision, per-source rejection accounting. |
| Retrieval on gold-seeded corpus | **Recall@10** for all relevant supporting IDs, plus **nDCG@10** for graded relevance | Precision@10, MRR for single-answer tasks, lexical/vector overlap, empty-result accuracy, language and category slices, p50/p95 latency, index freshness. |
| Temporal resolution | **State correctness** across current, `as_of`, `known_at`, ambiguity/conflict and abstention cases | Import permutation consistency, correction-vs-change, precision-window cases, overflow behavior. |
| Answer | **Grounded answer correctness** and **citation correctness** against source truth cards | Abstention accuracy, unsupported statement count, context loss, missed-retrieval rate. |

Avoid a single blended score. Report numerator/denominator, Wilson or bootstrap intervals, category slices, and the exact corpus/fixture/model/client versions. Use blinded human adjudication for ambiguous answer judgments; an LLM judge may triage but cannot supply ground truth. No production query log or private assertion contents enter CI.

## Baseline and decision procedure

1. Pin baseline commit, migration, embedding model digest, client/skill version, corpus snapshot, machine class and query set. Run disposable PostgreSQL/pgvector with deterministic fake embeddings for contract tests; run the local production model on an isolated test host for quality measurements. Record model load separately from steady-state latency.
2. For each case, capture extraction outputs, append/commit receipts, rejected items, indexing completion, search candidates and order, temporal resolver output, selected context IDs, final answer, citations and adjudicated labels. Keep private text in local fixture storage; publish only aggregate and synthetic examples.
3. Measure baseline with the existing search, then one change at a time: lexical query mode, candidate pool, fusion, filtering, threshold, and optional reranking. Evaluate on dev first, held-out once per chosen candidate. Include degraded embedding path and p95 latency.
4. Use a paired comparison over the same cases. Initial release gate: no critical security/temporal invariant regression; no more than 2 percentage-point loss in overall Recall@10 or grounded answer correctness, no more than 5 points in a protected category, and p95 search latency within an agreed budget (provisional 500 ms steady-state on reference host). Require statistically supported gain or a clearly documented usability fix to accept complexity. Replace provisional thresholds after observing baseline variability.
5. Persist a redacted machine-readable result and a human-readable failure taxonomy. Rerun at each retrieval/client-policy change and after temporal merge. CI runs synthetic smoke cases; a periodic operator-local full suite includes private cases.

Observed live retrieval and usage counts are **not** baseline quality metrics. No trustworthy recall, precision, extraction coverage, or answer correctness number was available at review time.
