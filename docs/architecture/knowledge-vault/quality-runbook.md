# Running the synthetic quality baseline

`scripts/quality_baseline.py` runs only against the disposable PostgreSQL 17/pgvector started by
`tests/conftest.py`. It has no database URL argument. Its gold source packs and expected claims are
in `tests/quality/corpus.py`; expected relevance and temporal state are authored there before the
server ingests anything. The 63 packs span eleven categories and use template siblings, so the result
is a reproducible **synthetic baseline**, not a population estimate or a two-reviewer adjudication.

```bash
uv run python scripts/quality_baseline.py --full --mode text --distractors 200 \
  --revision COMMIT_SHA --report /tmp/kv-quality-text.json
uv run python scripts/quality_baseline.py --full --mode fake --distractors 200 \
  --revision COMMIT_SHA --report /tmp/kv-quality-fake.json
```

`text` measures PostgreSQL lexical retrieval. `fake` exercises the vector pipeline with a
deterministic hash provider: its vectors have **no semantic meaning** and must never be used to
judge production embedding quality. `local` loads the configured model from local files only,
fails if absent, and never downloads weights. Run it on a host with the production model cache
and enough memory; record the exact model cache revision separately because the report stores the
configured model identifier, not a weights digest. The default invocation runs one smoke case per
category. Full results include per-case synthetic keys but no assertion text, source text, query
text or credentials. Keep even synthetic reports out of production telemetry.

Use `--split dev` during candidate selection and run `--split held_out` once for the selected
candidate. Each invocation seeds only that split; compare it with an unchanged-search run using
the same split and distractor count.

The `dev` and `held_out` labels are fixed by scenario; do not tune on held-out outcomes. The
report's binary nDCG@10 and Recall@10 score retrieval over preseeded gold assertions. Temporal
correctness scores the separate exact-identity resolver. Three missing-model cases label the
record that says the model is unknown as relevant evidence; **abstention** is judged separately
from the client answer. A retrieval system should not be rewarded for hiding that evidence.
Three additional questions have no relevant record at all. Empty-result accuracy diagnoses
candidate noise on those cases; a client may still abstain after seeing irrelevant results.
p50/p95 are local steady-state search timings, not a deployed latency SLA.
The fixed-seed case-bootstrap intervals describe variation within these templates only; they
are not population confidence intervals. Index readiness is reported as ready assertions for
the configured model out of all seeded assertions. The report includes no temporal values.

**Not yet measured:** source-to-assertion extraction, answer correctness, citation correctness and
client abstention. Those require independently judged outputs from a real assistant/client; no
backend test can certify them. Do not report the synthetic baseline as end-to-end quality. For a
private extension, keep original source packs and reviewer labels in an encrypted operator-local
store outside Git/CI, adjudicate before running the system, and publish only aggregate counts.

To score such a client run, create an operator-local JSON list with one entry per case. Each entry
has `case_id`, two `reviewers` with distinct `reviewer_id` values, and an `adjudicated` review.
A review may supply `matched_gold_keys` (keys of claims actually extracted),
`unsupported_claims`, `answer_correct`, `grounded`, `citations_correct`, and `abstained`.
Missing fields remain unmeasured. Annotators must read the original source and client output,
without viewing the backend benchmark result or each other's initial labels. Run:

```bash
uv run python scripts/quality_score_judgments.py /operator-local/judgments.json \
  --report /tmp/kv-quality-judgments.json
```

The scorer validates gold keys, rejects repeated cases, reports agreement and split-level metrics,
and emits no source, assertion or answer text. It cannot verify that reviewers were truly
independent; the operator must preserve that review process.

Before changing ranking, compare the same corpus, model and machine. Use the failure IDs in the
redacted report to inspect synthetic cases. A ranking change belongs in a separate M2 PR and must
show paired results plus the repository's full verification gates.
