# M2 synthetic retrieval experiment — 2026-10-31

The frozen M1 source-first corpus was run with 200 unrelated synthetic distractors against
disposable PostgreSQL 17/pgvector at migration `0006_temporal_evidence`. Development and held-out
packs were seeded **separately**, so each pair below uses the same 269 or 236 assertions. The
unchanged-search control loaded merged main's production search code. The selected candidate is
the [conjunctive question fallback](../../decisions/0008-conjunctive-question-search.md). No
production knowledge or model weights were read. The local fake-vector provider is deterministic
but has no semantic meaning.

| Split and path | Relevant cases | Recall@10 | Binary nDCG@10 | Empty result | Temporal | Local p95 |
|---|---:|---:|---:|---:|---:|---:|
| Development, unchanged text | 31 | 0.000 | 0.000 | 1/1 | 13/13 | 5.27 ms |
| Development, question fallback text | 31 | 0.871 | 0.819 | 1/1 | 13/13 | 7.35 ms |
| Held-out, unchanged text | 14 | 0.000 | 0.000 | 2/2 | 8/8 | 7.30 ms |
| Held-out, question fallback text | 14 | 0.786 | 0.745 | 2/2 | 8/8 | 30.13 ms |
| Held-out, unchanged fake vectors | 14 | 0.036 | 0.019 | 0/2 | 8/8 | 18.76 ms |
| Held-out, fallback + fake vectors | 14 | 0.786 | 0.719 | 0/2 | 8/8 | 30.53 ms |

The held-out control was run after selecting the candidate, solely to make a paired comparison.
No candidate was tuned on held-out results. The 14 relevant held-out cases include two with
multiple required records.
The three remaining text misses are one product preference and two multilingual queries.

These source packs are template siblings authored by one implementer, and the held-out confidence
interval for text Recall@10 is [0.571, 1.000] only under case bootstrap over those templates.
Neither this interval nor the gain estimates production prevalence. Client extraction, answer
groundedness, citation correctness and abstention remain unmeasured. The local production
embedding model was absent, so the deployed hybrid path still requires an operator-local model
run and reviewed private cases before release. The vector-only no-record noise is a separate
ranking/abstention issue; this fallback does not solve it.

The rejected broad OR experiment and the original baseline are recorded in
[M1 results](baseline-2026-10-31.md). Future ranking candidates must use a new frozen case set;
these held-out templates have now been inspected. Run the benchmark with `--full --split dev` or
`--full --split held_out` as described in the [quality runbook](quality-runbook.md).
