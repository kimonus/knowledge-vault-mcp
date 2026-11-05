# Client evaluation protocol: solo operator and optional independent review

`solo_operator` is the default M1 engineering acceptance workflow for this single-user
deployment. One recorded evaluator can combine deterministic checks, explicitly identified model
judgments and manual review of ambiguous/failed cases. `independent_review` preserves the
two-human, blinded first-pass/adjudication protocol as optional research-grade validation.
Evaluation mode and retrieval arm are separate: both modes support `lexical_only` or `hybrid`;
never pool their results. Neither mode needs an embedded generative LLM.

The backend benchmark and judgment scorer do not run a real MCP assistant. This is the protocol
for an operator-run, blinded client study. The owner has deferred a new release until the
completion program finishes. T0 pre-release checks can use an isolated non-production deployment;
production validation is a separate post-deployment gate.

## Prepare blinded development packets

```bash
uv run python scripts/quality_client_packets.py --split dev --repetitions 3 \
  --output /operator-local/kv-client-dev-packets.json
```

The 41 development cases produce 123 run packets, three per case. Each packet gives `case_id`,
`run_id`, source, optional question and optional temporal request. It excludes gold claims,
relevance keys, expected state, answer and abstention labels. The script refuses to overwrite an
existing packet file. Check the corpus digest, `case_count=41`, `run_count=123`, 123 unique
`(case_id, run_id)` pairs, and `r01`/`r02`/`r03` for each case. Keep the packet file outside Git.
The existing held-out packs are template siblings already inspected during M2; they are not
fresh blind evidence for ranking selection. Freeze a new source-scenario held-out set before later
tuning. Do not show the current truth cards or backend report to the client.

## Operator prerequisites and experiment manifest

The solo operator supplies one recorded evaluator, an authenticated MCP client with the current skill, a
disposable PostgreSQL 17/pgvector-backed Knowledge Vault, and an access-controlled operator-local
directory for packets, traces, truth review, judgments and results. Obtain consent for any real
source cases and keep them in that local store. Do not use production knowledge or credentials or
place private source material, traces or judgments in Git or CI.

For `independent_review`, additionally supply two actual independent human reviewers and an
adjudicator (who may be one reviewer after the first passes are sealed). Changing the mode does
not turn machine judgments, two model sessions, or copied audit files into independent people.

Use two separately reported experimental arms: `lexical_only` disables embeddings and needs no
local model; `hybrid` requires an independent verified offline embedding snapshot. Both
may evaluate an actual assistant client's extraction, grounded answers, citations, temporal
routing and abstention. Never pool arm metrics or interpret lexical-only observations as
semantic-search quality. Neither requires an on-server generative LLM. A real assistant
client is still required; the two-human requirement applies only to `independent_review`.

Record a manifest before running cases: UTC date, selected case IDs, packet/corpus SHA-256, run
order and random seed, client name/version, client model and exact settings, MCP skill version or
digest and installed path, server commit/image digest, Alembic revision, `retrieval_mode`, embedding
model name and local weight revision/digest (null in lexical-only mode), PostgreSQL version,
host CPU/RAM, index readiness where applicable, and whether model load time is included in timing. Use one configuration for all three repetitions. A fake vector
provider verifies plumbing but cannot support semantic quality claims. If the client cannot be
automated, record manual operator actions and repeat them consistently.

## Execute each run

For **every case and repetition**, use a fresh client context and a fresh disposable vault (or a
new empty database with verified zero assertions and temporal evidence). Confirm the reset before
presenting the packet. Supply only the blinded packet source. Ask the client explicitly to flush
knowledge to its disposable MCP, then ask the packet's question or current/historical state
request. Keep gold cards out of the client context. Capture the tool sequence, emitted assertion
items, per-item rejection and commit/readback receipts, accepted IDs, indexing completion,
retrieved IDs and order, temporal resolver result, actual context shown to the assistant, final
answer, citations, and abstention. Link each trace to its run ID and manifest. Preserve traces
outside Git and CI; publish only redacted aggregates and synthetic examples.

Run the gold-seeded backend retrieval and temporal benchmark separately in its own disposable
database. It measures retrieval and resolver behavior without extraction omissions. Never score
retrieval only against assertions the client happened to store. For the client path, also record
which relevant IDs reached the assistant's context; that is a separate routing observation.

## Solo-operator truth audit and judgments (default)

Generate readable cards without duplicating the corpus:

```bash
uv run python scripts/quality_truth_audit.py --evaluation-mode solo_operator \
  --split dev --output /operator-local/m1/truth-audit
uv run python scripts/quality_score_judgments.py --schema truth-audits \
  > /operator-local/m1/truth-audit-schema.json
```

`cards.md` presents source text, expected claims, dates and temporal requests for a plain-language
audit. The JSON template is deliberately incomplete and counts as zero reviews. Audit each
source against its truth card, especially entity aliases, observation dates and effective dates;
do not invent a missing identity mapping or timestamp to match a fixture. Keep cards out of the
assistant's context. If a source/card needs correction, record `needs_revision` with notes and
freeze a new experiment revision before collecting replacement results; preserve the old traces.
The 41 development cases and 123 planned packets remain unchanged by this workflow change.

One `TruthAudit` receipt records `case_id`, opaque `audit_id`, `reviewer_id`, source SHA-256,
`artifact_sha256`, provenance, `verdict`, `independent`, `before_client_output`, and optional notes.
Hash the separately completed review artifact (for example the evaluator's dated Markdown
decisions), not the receipt JSON containing its own digest. An accepted source audit requires
a human or model evaluation: a mechanical hash/schema check cannot certify semantic truth.
Retrospective solo audits are allowed but their timing is reported; never relabel them as blind
prospective validation. Model-assisted reviews remain `kind=model` unless a real person performs
and records a separate human review. The same person may be the evaluator and operator.

Solo judgment entries have this shape (all hashes must be actual receipts, not placeholders):

```json
{
  "case_id": "decision-0",
  "run_id": "r01",
  "review": {
    "reviewer_id": "operator",
    "provenance": {
      "kind": "model",
      "method": "record the judge model/version, prompt revision and procedure",
      "recorded_at": "2026-11-05T12:00:00Z",
      "evidence_sha256": "<SHA-256 of this client trace>"
    },
    "answer_correct": true
  },
  "checks": [],
  "needs_manual_review": false
}
```

This illustrative JSON is not a judgment of an actual run. Leave absent semantic fields null
or omitted until evaluated. `checks` contains named `{name, passed, provenance}` deterministic
checks, such as receipt consistency, empty initial DB, expected retrieval mode and cited-ID
existence. Such checks do not certify answer correctness, grounding or source coverage.
Deterministic reviews may compare exact retrieved gold IDs or resolver outcomes; they cannot
populate semantic review labels. Model/human label metrics are separated in the report.

Set `needs_manual_review=true` when an ambiguous or failed case needs human inspection. Check
failures alone do not mandate a human or make recorded evidence incomplete; they remain visible
for the operator's quality decision. A manual review uses the same label/provenance shape
with `kind=human`; it supplies the effective metric labels while preserving the original machine
review and check failures. It does not erase a failed check or fabricate an adjudication.

## Independent truth and judgment (optional research mode)

Two reviewers independently read each original source and audit its preauthored truth card
**before viewing the client output**. Preserve both initial truth labels, adjudicate disagreements,
and freeze the card. A preauthored synthetic card is not independently validated merely because it
predates a run. Then both reviewers independently inspect the client output and trace without
seeing the backend score or each other's initial labels. Seal both dated first-pass reviews before
adjudication and record the decision. The scorer checks distinct reviewer IDs only; it cannot
establish that two people worked independently, were blinded, or saw a real client run. The
operator must verify those facts. Synthetic scorer test fixtures are not human judgments.
The strict mode still requires exactly two distinct reviewer IDs and an adjudicated review.
Recorded machine provenance is refused in those human slots. Source-truth receipts must include
two distinct human evaluators, `independent=true`, `before_client_output=true` and separately
sealed audit artifacts per case. Reused audit IDs, repeated reviewer IDs and identical artifact
digests for the same case are rejected, including renamed copies of the same audit JSON.
Identical decisions by two people with distinct genuine artifacts are legitimate agreement.
The scorer cannot prove that attestations describe two actual people.

For each `case_id` and `run_id`, the optional judgment fields are:

| Field | Labeling rule |
|---|---|
| `source_outcome` | `reviewed`, `partial`, `unprocessed`, or `unavailable`. The latter is counted but excluded from available-source coverage. Missing means unjudged. |
| `matched_gold_keys` | Gold claims actually captured in the committed flush with required detail and epistemic label. `[]` means observed zero; omitted means unjudged. |
| `unsupported_claims` | Emitted assertion items unsupported by the original source, including server-rejected ones. Supply with `matched_gold_keys`. |
| `extracted_claims_count` | All emitted assertion items, including rejected ones; needed for unsupported-claim rate. If no items were emitted, enter zero rather than omitting the label. |
| `retrieved_gold_keys` | Relevant gold keys among the client's retrieved top-ten context; `[]` means observed miss. This does not replace the gold-seeded backend retrieval score. |
| `temporal_correct` | Whether observed state resolution matches the adjudicated outcome and values; use only for state cases. |
| `unresolved_handling_correct` | For a `conflict`, `ambiguous`, or `unknown` state, whether the client accurately explains why no definitive value is established. A conflict explanation must identify competing evidence; a generic unsupported refusal is false. Use only for unresolved state cases. |
| `answer_correct`, `grounded`, `citations_correct` | Judge final answer, evidential grounding and citations separately against the source truth. Null is unjudged, never false. |
| `abstained` | Whether the client declined to give a definitive value. True is correct on an evidence-insufficient abstention card and an error on a definitive-value card. For an unresolved state, score the explanation with `unresolved_handling_correct`, grounding and citations instead of the abstention-rate metrics. |

Response expectation has three classes: **definitive value** for a resolved state or an answerable
ordinary question; **abstention** for a card explicitly requiring it; and **unresolved explanation**
for a temporal `conflict`, `ambiguous`, or `unknown` outcome. The conflict cards `conflict-3` through
`conflict-5` are in the third class even though their old `requires_abstention` flag is false.
The scorer derives this from the existing truth card, so the corpus and baseline digest are
unchanged. A definitive-value case cannot have both `abstained=true` and `answer_correct=true`.
An abstention card or a well-grounded conflict explanation can have `answer_correct=true` while
declining to choose a value. A generic refusal on a conflict is not a correct explanation. Record
retrieval, temporal, answer and citation judgments separately even when one upstream stage fails.
Keep the first-pass cards and adjudication decisions in operator-local storage.

## Score and interpret

```bash
uv run python scripts/quality_score_judgments.py \
  /operator-local/kv-client-judgments.json \
  --evaluation-mode solo_operator --retrieval-mode lexical_only \
  --packets /operator-local/kv-client-dev-packets.json \
  --truth-audits /operator-local/m1/truth-audits.json \
  --client-runs /operator-local/m1/client-runs.json \
  --markdown /operator-local/m1/summary.md \
  --report /operator-local/kv-client-judgments-summary.json
```

Select `--evaluation-mode independent_review` explicitly for legacy two-reviewer input. Its
entries retain `case_id`, optional `run_id` (default `default`), two distinct `reviewers` and an
`adjudicated` review. Solo entries use `review` and optional `manual_review` instead, with no
synthetic second reviewer. The scorer rejects duplicate case/run pairs
and invalid gold keys. Pass the **exact generated blinded packet JSON** with `--packets` for
expected-run validation. It checks its corpus digest, split, counts and complete packet contents
against the current truth-card corpus, then compares `(case_id, run_id)` pairs. A changed or stale
manifest is rejected without echoing source text. Extra judgment runs appear in completeness but
are excluded from planned-run quality metrics; duplicate pairs are rejected. The report gives
expected, observed, missing and unexpected run counts and zero-based missing packet indices, which
let the operator locate gaps in the private packet file without publishing IDs or source text.
`run_completeness.status` is `complete`, `partial`, or `unchecked`; the last applies to old
judgment files scored without `--packets`. `study_complete` is true only when a manifest matches
all observed runs exactly and no eligible metric label is missing. `balanced_runs=true` alone does
not establish completeness. Partial scores remain available and must be reported as partial.

The scorer also reports unique cases, run balance, reviewer agreement (independent mode only) and
redacted aggregates. Solo final reviews or independent adjudicated labels enter outcome metrics.
Schema v3 retains old flat fields as
per-run averages for existing consumers and adds `metrics` and `metrics_by_split`. Each metric has
`numerator`, `denominator`, `eligible_runs`, `labeled_runs`, `missing_labels`, and `value`; a zero
denominator yields null. Development and held-out counts remain distinct.

Every JSON/Markdown report states `evaluation_mode` and `retrieval_mode`. `provenance` counts
submitted and effective human/model/deterministic/unrecorded reviews separately, and
`metrics_by_judgment_kind` prevents machine judgments from masquerading as human results.
Legacy independent cards without provenance still score, but cannot establish complete acceptance
evidence. The Python `score_judgments` API retains its strict legacy default for compatibility;
the M1 CLI defaults to solo and new callers should always select their mode explicitly.

`--client-runs` supplies recorded actual assistant execution receipts, not SDK/backend smoke
results: case/run, client name/version/model, server/schema revisions, retrieval arm, embedding
model (null in lexical-only mode), corpus/source digests and trace reference/SHA-256. Source and
corpus digests must match the frozen inputs; all review/check provenance must bind to that run's
trace digest. Duplicate receipts and mixed retrieval arms are refused. Keep the actual private
trace available for audit; the scorer validates receipt consistency, not authorship/authenticity.
Export all machine-readable schemas with `--schema judgments|truth-audits|client-runs` and the
chosen `--evaluation-mode`. Do not use schema-valid metadata as proof that an assistant ran.

`study_complete` retains its label-completeness meaning, not a passed quality gate.
`acceptance_evidence_complete` additionally requires all planned client receipts, trace-bound
provenance, accepted source audits and no explicitly pending manual reviews. Failed checks remain
reported even when evidence is complete; their severity belongs to the acceptance decision.
It remains false for generated packets, empty judgments or unreviewed pilot fragments. The scorer
always reports `quality_gate_status=not_decided_by_scorer`: the solo operator records a separate
accept/reject decision against the benchmark thresholds, failure taxonomy and limitations only
after actual client results have been collected and scored. Evidence completeness is not quality.

Gold-claim coverage is matched required claims / required claims on labeled runs with an available
source; explicitly unavailable packs are excluded from that denominator. Source coverage
is fully reviewed available source packs / available source packs with a label; partial and unprocessed
remain in its denominator, unavailable is excluded and counted separately. Unsupported-claim
rate is unsupported emitted claims / all emitted claims only where an explicit item count exists;
the separate total count still includes older cards without a denominator. Correct abstention is
abstentions / labeled cases that require abstention. **Incorrect abstention** is abstentions /
labeled definitive-value cases, where lower is better. Answerable answer correctness excludes
required-abstention and unresolved-state cases. Unresolved handling is scored separately, so a
supported conflict explanation cannot inflate incorrect abstention. Grounding, citation
correctness, retrieval recall and temporal correctness have
separate counts. Missing labels never become negative outcomes.

The scorer does not generate confidence intervals or prove independent review. Research-grade
external validation additionally needs category/language slices, uncertainty intervals, a redacted failure
taxonomy, versioned manifest and denominator review. The 63 synthetic source packs share templates
and are not representative real-user prevalence estimates. No actual two-reviewer client results
are supplied by this protocol; M4/M5 and any server source-receipt table remain conditional on
measured failures. Personal M1 acceptance uses the reproducible solo workflow; it does not require
recruiting two people or claim research-grade generalization.
