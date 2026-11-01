# M1 — Independent knowledge quality baseline

**Priority:** first new implementation milestone after T0. **Effort:** M–L (roughly 2–4 weeks for fixtures, harness and adjudication), medium confidence. **Dependencies:** T0 completed; independent branch/worktree. **Owner:** quality/evaluation agent.

## Problem and evidence

Current contract and invariant tests verify storage and adapter behavior; they do not measure whether useful source claims are extracted, relevant records retrieved, or answers grounded. Live statistics report 581 retrievals and zero uses, but neither number measures correctness ([assessment](../assessment.md)). Without independent labels, ranking or schema changes cannot be justified.

## Scope and architectural decisions

Implement [benchmark specification](../benchmark.md) with source-first, independently adjudicated gold truth. Separate extraction, retrieval on gold-seeded data, temporal resolution and assistant answer evaluation. Commit synthetic fixtures; store real private cases outside Git. Do not change production schemas, ranking or prompts to make a baseline look good. Avoid using vault output to author labels.

## Implementation requirements

Add `tests/quality/` case schema, at least 60 synthetic packs spanning ten benchmark categories, stable fixture keys, source truth cards, labeling guide and adjudication log. Add a disposable PostgreSQL/pgvector seeder and a CLI runner with pinned versions. Score extraction claim coverage, Recall@10/nDCG@10, temporal correctness, grounded answer/citation correctness and abstention; record latency and index readiness separately. Support deterministic backend runs and repeated client samples where generation varies. Produce redacted JSON/Markdown reports with per-category counts and failure taxonomy. Add CI smoke cases and operator-local full-run instructions. Reuse existing integration fixture/test infrastructure where practical.

## Acceptance and testing

Two reviewers can label a case before ingestion and resolve disagreement without seeing system results. Gold-seeded retrieval scores are unaffected by extractor omissions. Include import permutations, alternate query languages, 200 distractors, deletion-index checks, missing evidence, conflicts, and proposal-vs-purchase. Run on real disposable PostgreSQL 17/pgvector; no model download in tests. Existing lint, type, unit/property/contract/integration/e2e and chart gates pass where applicable. Publish baseline denominators and confidence intervals, not an unsupported single quality score. If a hosted-client harness is inaccessible, clearly label answer evaluation manual and still deliver backend baseline.

## Migration, compatibility, risk and rollback

No production model/API/migration changes. Synthetic fixtures are safe for Git; private case records must not enter logs, CI artifacts or repository. Potential risks are annotation bias, source leakage, and assistant nondeterminism. Freeze held-out cases and record seeds/model/client versions. Rollback removes the additive benchmark package; it has no live data effect.
