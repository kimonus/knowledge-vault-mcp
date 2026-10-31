# Document A — Executive architecture assessment

## Verdict

Knowledge Vault has a solid **durability and security foundation**, with a less mature **knowledge quality loop**. PostgreSQL transactions, bounded MCP/HTTP contracts, explicit correction, a persistent embedding queue, scoped authentication, and tested hard deletion protect submitted records. The system cannot establish that a conversation was fully extracted or that retrieved records support a correct answer. That distinction governs this roadmap.

The audit baseline is `497ad3c`; temporal branch `ce6d35a` is separate. A branch report claims 253 passing tests and 98.14% statement / 93.26% branch coverage for temporal changes, but this review did not independently run them or inspect a deployed temporal endpoint. See [temporal checkpoint](milestones/T0-temporal-integration.md).

## Evidence ledger

| Finding | Status | Evidence | Consequence / limit |
|---|---|---|---|
| Transactional, resumable ingestion and exact text identity exist | Observed | [Ingestion service](../../../src/knowledge_vault/services/ingestion.py), [domain input](../../../src/knowledge_vault/domain/models.py), [PostgreSQL integration tests](../../../tests/integration/test_postgres_flow.py) | A commit proves persistence of submitted items, not source coverage. Exact normalized text is globally unique; paraphrases remain separate. |
| Server does not receive original conversation or source inventory | Observed | [Original brief](../../../personal-knowledge-mcp-codex-prompt.md), [flush skill](../../../plugin/knowledge-vault/skills/flush-knowledge/SKILL.md), [MCP begin/append contract](../../../src/knowledge_vault/mcp/server.py) | Extraction completeness is client responsibility and cannot be inferred from commit counts. |
| Search is hybrid, but candidate generation and fusion have no semantic relevance floor | Observed | [Search service](../../../src/knowledge_vault/services/search.py), [ranking](../../../src/knowledge_vault/services/ranking.py), [ADR 0003](../../../docs/decisions/0003-hybrid-retrieval.md) | Multiword lexical AND behavior and vector-only candidates can produce weak results; user-visible impact remains to be measured. |
| Legacy status and valid-at filters do not reconstruct state | Observed | [Search filters](../../../src/knowledge_vault/domain/models.py), [search implementation](../../../src/knowledge_vault/services/search.py), [maintenance semantics](../../../docs/knowledge-maintenance.md) | A current search hit is not necessarily what is currently true. Temporal branch addresses this with structured evidence and a resolver; do not duplicate it. |
| Conflict registration is narrow | Observed | [Negation matcher](../../../src/knowledge_vault/services/conflicts.py), [ingestion conflict path](../../../src/knowledge_vault/services/ingestion.py) | Zero registered conflicts cannot establish a contradiction-free corpus. Different values of the same property generally evade negation matching. |
| Provenance and epistemic metadata are optional and legacy defaults are unknown | Observed | [Metadata model](../../../src/knowledge_vault/domain/maintenance.py), [assertion model](../../../src/knowledge_vault/domain/models.py), [migration 0005](../../../migrations/versions/0005_knowledge_maintenance.py) | Source traceability and evidence quality depend on client extraction. Numeric confidence 1.0 is a default, not verification. |
| Usage accounting measures returns, not answer utility | Observed | [Maintenance semantics](../../../docs/knowledge-maintenance.md), [usage service](../../../src/knowledge_vault/services/maintenance.py), [metrics](../../../src/knowledge_vault/observability/metrics.py) | Search calls, successful use, missed retrieval, and answer correctness cannot be equated. |
| Security and recovery controls are substantial | Observed | [Threat model](../../../docs/threat-model.md), [auth modules](../../../src/knowledge_vault/auth/), [backup runbook](../../../docs/runbooks/backup-restore.md), [contract tests](../../../tests/contract/test_http_stack.py) | Remaining risks include single-node failure, false-negative secret detection, and deleted content persisting in older encrypted backups until retention. |

## Read-only operational snapshot

`get_knowledge_statistics` returned **2,469** assertions: **2,035 current**, **366 uncertain**, **68 superseded**, and **0 registered unresolved conflicts**. It reported **2,469 completed embedding jobs**, worker heartbeat age about 86 seconds, backup heartbeat age about 11.4 hours, 581 recorded retrievals, zero explicit fetches and zero reported uses, 0 contexts/aliases/review-due records, and 2 artifacts. These figures are one live snapshot; completed jobs are not a direct proof that every vector is usable by the current model.

Four read-only searches (`Minikube ingress configuration`, `purchased laptop`, `backup restic`, `router WiFi channel`), each limited to 20 and marked `purpose=maintenance`, produced 80 hits representing 76 unique assertions. By query, missing `observed_at` counts were 17/20, 16/20, 9/20, 17/20; missing both typed provenance and URL source counts were 17/20, 18/20, 20/20, 20/20. Every returned hit had `evidence_state=unknown`, `temporal_role=unspecified`, and `status=current`. This is a biased search sample, **not** a corpus prevalence estimate. The top 10 scores in each query equaled the vector-only term of weighted reciprocal-rank fusion (`0.45/(60 + rank)`), consistent with no lexical candidates entering those result sets. It does not prove the semantic results were irrelevant; private contents were not published or scored against independent relevance judgments.

## Most serious weaknesses

1. **No independent quality or coverage evidence.** This is confirmed by architecture and telemetry: the server receives submitted assertions, and the repository has contract/invariant tests but no held-out extraction/retrieval/answer benchmark. It is the first improvement target after temporal integration.
2. **Discovery can fail without signaling low relevance.** Candidate generation and score behavior are confirmed; frequency and answer impact are hypotheses. Do an ablation before changing weights, model, or index.
3. **Weak traceability and applicability in legacy data.** Optional metadata and the sampled blanks are confirmed. The population-wide rate and actual impact need measurement. Temporal work supplies deterministic state resolution only for explicitly structured evidence; it does not automatically classify legacy assertions.

## Existing strengths and operational debt

The worker claims jobs with leases and model matching; text fallback survives model failure ([worker](../../../src/knowledge_vault/worker/jobs.py)). The model is loaded locally and serially; no approximate vector index is currently required for a corpus of this size ([embedding runbook](../../../docs/runbooks/embeddings.md), [data model](../../../docs/data-model.md)). Encrypted restic backups, a watchdog, migration Jobs, NetworkPolicies, secret scanning, and CI gates are documented and tested ([CI](../../../.github/workflows/ci.yml), [monitoring](../../../docs/runbooks/monitoring.md)). An actual restored production snapshot and authenticated deployed-path temporal call were not performed in this review. The system still runs on one physical node; restore drills and operator-held credentials remain material recovery dependencies.

## Immediate action

Merge and verify the temporal work independently, then build a small benchmark with ground truth outside the vault. Use its failure cases to decide whether to change search, ingestion metadata, assistant behavior, or all three. Avoid bulk legacy backfill and automatic entity merging until there is reviewed evidence.
