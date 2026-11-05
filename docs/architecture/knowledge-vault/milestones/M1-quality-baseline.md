# M1 — Reproducible personal knowledge quality baseline

**Priority:** first new implementation milestone after T0. **Effort:** M–L (roughly 2–4 weeks for fixtures, harness and adjudication), medium confidence. **Dependencies:** T0 completed; independent branch/worktree. **Owner:** quality/evaluation agent.

## Problem and evidence

Current contract and invariant tests verify storage and adapter behavior; they do not measure whether useful source claims are extracted, relevant records retrieved, or answers grounded. Live statistics report 581 retrievals and zero uses, but neither number measures correctness ([assessment](../assessment.md)). Without independent labels, ranking or schema changes cannot be justified.

## Scope and architectural decisions

Implement [benchmark specification](../benchmark.md) with source-first truth auditing. The approved
default for this single-user deployment is `solo_operator`: one recorded evaluator, deterministic
checks, explicitly labeled model assistance and manual review of ambiguous/failed cases.
`independent_review` remains the optional strict two-human research protocol. Separate extraction,
gold-seeded retrieval, temporal resolution and assistant answer evaluation. Commit synthetic
fixtures; keep real private cases outside Git. Do not change production schemas, ranking or prompts
to make a baseline look good. Avoid using vault output to author truth labels.

## Implementation requirements

Add `tests/quality/` case schema, at least 60 synthetic packs spanning ten benchmark categories, stable fixture keys, source truth cards, labeling guide and adjudication log. Add a disposable PostgreSQL/pgvector seeder and a CLI runner with pinned versions. Score extraction claim coverage, Recall@10/nDCG@10, temporal correctness, grounded answer/citation correctness and abstention; record latency and index readiness separately. Support deterministic backend runs and repeated client samples where generation varies. Produce redacted JSON/Markdown reports with per-category counts and failure taxonomy. Add CI smoke cases and operator-local full-run instructions. Reuse existing integration fixture/test infrastructure where practical.

## Acceptance and testing

Solo acceptance requires a frozen 41-case/123-packet development experiment, auditable source
truth decisions, actual isolated assistant-client traces and scored judgments with explicit
missing labels and machine/human provenance. The operator records an acceptance decision against
the benchmark thresholds after reviewing failures. Complete packets or passing infrastructure
checks alone never pass M1. No independent-human-validation claim follows from solo acceptance.
Optional `independent_review` retains two blinded human first passes and adjudication; copied audit
files cannot satisfy independence. Both modes report lexical-only and hybrid results separately.
Gold-seeded retrieval scores remain independent of extractor omissions. Include import permutations,
alternate languages, 200 distractors, deletion checks, conflicts and proposal-vs-purchase cases.
Use disposable PostgreSQL/pgvector without model downloads. Required repository gates still apply.
Publish denominators and uncertainty, not a blended quality score. Unavailable clients remain an
explicit blocker to client acceptance; backend results can still be delivered separately.

## Migration, compatibility, risk and rollback

No production model/API/migration changes. Synthetic fixtures are safe for Git; private case records must not enter logs, CI artifacts or repository. Potential risks are annotation bias, source leakage, and assistant nondeterminism. Freeze held-out cases and record seeds/model/client versions. Rollback removes the additive benchmark package; it has no live data effect.
