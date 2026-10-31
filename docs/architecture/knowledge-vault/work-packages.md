# Document F — Implementation work packages

Each package owns a bounded subsystem and can be assigned to a separate coding agent after its dependencies are met. No package in this review was executed. Use an isolated branch/worktree, preserve unrelated work, update relevant docs/contracts/changelog, and run the repository gates in `AGENTS.md`. Production changes require the user's separate authorization.

| Package | Deliverables / likely files | Dependencies | Compatibility and risk |
|---|---|---|---|
| T0 temporal integration gate | Reviewed PR/merge evidence, disposable upgrade/restore report, authenticated deployed-path resolution proof, client schema refresh; temporal branch files and `docs/temporal-knowledge.md` | Temporal implementation owner and normal PR process | Migration/deployment approval; do not edit the implementation agent's worktree. |
| M1 quality baseline | `tests/quality/` synthetic fixtures and labels, offline runner/report, extraction/client harness, [benchmark](benchmark.md) revisions | T0 | No production schema/API; private real cases stay outside Git. |
| M2 retrieval experiment | Search ablation runner and one measured search/ranking change, shared response changes only if needed, contract/integration tests | M1 frozen held-out set | Existing MCP clients continue to work; reversible ranking behavior. |
| M3 coverage/provenance | Client checklist first; optional new migration/source receipts, shared models, MCP/HTTP parity, deletion/restore tests | M1 and T0 | Additive API; strong privacy and retention review. |
| M4 targeted curation | Reviewed case list, metadata/temporal enrichment and alias proposal tooling, regression fixtures | M1 and T0 | No automatic data rewrite, date invention or silent entity merge. |
| M5 feedback/freshness | Opt-in client feedback contract, safe aggregates, review policy docs/tests | M1; preferably M2/M3 | No content in metric labels/logs; no TTL deletion. |

## Reusable task brief: first recommended implementation milestone M1

**Role:** Implement the independent Knowledge Vault quality baseline described in [M1](milestones/M1-quality-baseline.md) and [benchmark](benchmark.md), after T0 is complete. Work on a new branch/worktree. Do not modify live knowledge. Do not alter search ranking or schema as part of this package.

**Inputs:** current merged temporal contracts, existing `tests/conftest.py` disposable pgvector fixture, `tests/integration/`, `tests/contract/`, `plugin/knowledge-vault/skills/flush-knowledge/`, and representative synthetic source packs. Confirm the merged temporal revision and client tool schemas before coding.

**Deliverables:** a versioned fixture format with original source, independently authored truth card, expected relevant IDs or stable fixture keys, allowed/required abstention, time semantics and privacy classification; at least 60 synthetic scenarios distributed across the [benchmark matrix](benchmark.md); deterministic seeding and extraction replay; separate retrieval, temporal and answer scoring; a CLI producing JSON and Markdown reports with version/machine/model metadata and redacted failures; a small CI smoke suite and instructions for an operator-local private suite. Keep quality labels out of the data presented to the system under test.

**Acceptance:** two reviewers can annotate/adjudicate a case without seeing system output; no score uses vault output as ground truth; run order and query language metamorphic cases; deletion, distractor, proposal/purchase and missing-evidence negatives; run on a real disposable pgvector PostgreSQL instance; calculate Recall@10, nDCG@10, temporal correctness, extraction claim coverage, grounded answer/citation correctness and abstention separately. Report numerator/denominator and uncertainty. The baseline must be reproducible from pinned fixture, code, client, model and database versions. The full existing quality/security gates remain green; document any unavailable client harness explicitly.

**Risks and limits:** gold labels are labor intensive; private cases cannot be committed; an assistant harness may be nondeterministic. Fix seed/configuration and rerun repeated samples for answer metrics. If the hosted client cannot be automated, measure server retrieval separately and conduct a documented manual client study. Do not claim end-to-end correctness from backend tests alone. Rollback is removal of this additive evaluation package; no migration or knowledge rollback exists.

## Guidance to later agents

Treat the standalone milestone file as the scope boundary. M2 should not absorb M3's provenance work; M3 should not tune retrieval; M4 should not bulk edit production data under a tooling task. Every proposed change must cite at least one M1 failure group and show a paired before/after result. Keep temporal semantics owned by T0. All domain/API response fields must be shared in `domain/responses.py`; migrations must be new Alembic revisions with explicit DDL. Preserve auth, secret detection, deletion, logs, backup and restore behavior. Record a feature's rollback path before deploy.
