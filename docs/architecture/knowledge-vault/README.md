# Knowledge Vault architecture review and evolution roadmap

Review date: 2026-10-31. Baseline: `497ad3c` (`ops/homelab-v0.5.0`). This is a planning deliverable; no application code, schema, runtime configuration, or persistent knowledge was changed. The temporal workstream was inspected read-only at `ce6d35a` on `feat/temporal-knowledge`; it is **not** a capability of the reviewed baseline until merged and deployed.

## Read in order

1. [Executive assessment](assessment.md) — evidence, strengths, risks, and uncertainty.
2. [End-to-end flow](flow.md) — ownership, failure and recovery by lifecycle stage.
3. [Quality benchmark](benchmark.md) — independent ground truth and decision gates.
   [Run the synthetic baseline](quality-runbook.md) and inspect its
   [first measured result](baseline-2026-10-31.md).
   The [M2 paired retrieval experiment](retrieval-experiment-2026-10-31.md) records the next
   measured change and its limits.
   The [M3 client source-coverage checkpoint](source-coverage-checkpoint.md) records the
   non-schema first step and the evidence still needed.
   Use the [synthetic client evaluation protocol](client-evaluation-protocol.md) to collect
   repeated assistant runs without showing the client gold labels.
4. [Target architecture](target-architecture.md) — incremental design and alternatives.
5. [Dependency-aware roadmap](roadmap.md) — order, effort, and measurable outcomes.
6. [Work packages](work-packages.md) — ownership map and reusable first-task brief.

Standalone milestone specifications:

| ID | Specification | Priority |
|---|---|---|
| T0 | [Temporal post-merge checkpoint](milestones/T0-temporal-integration.md) | Required gate |
| M1 | [Independent quality baseline](milestones/M1-quality-baseline.md) | First implementation milestone |
| M2 | [Measured retrieval improvement](milestones/M2-retrieval.md) | High, contingent on M1 |
| M3 | [Source coverage and provenance](milestones/M3-source-coverage.md) | High, contingent on M1 |
| M4 | [Targeted lifecycle and identity repair](milestones/M4-curation.md) | Conditional |
| M5 | [Feedback and freshness](milestones/M5-feedback-freshness.md) | Conditional |

**Critical path:** T0 → M1 → M2. M3 can follow M1 in parallel with M2 only in separate worktrees and with independent file ownership. M4 and M5 require measured demand. Neither a graph database nor a new embedding model is justified yet.

## Evidence convention

**Observed** means inspected code, documentation, tests, or a read-only service response at the review date. **Inferred** means a consequence of those observations that has not been measured end to end. **Hypothesis** means a plausible failure requiring benchmark evidence. Private assertion text and identifiers are deliberately absent from these documents. Live counts are snapshots, not stable product facts. Relative source links point to the reviewed checkout; temporal links point to its branch by commit name because its files are not part of this branch.
