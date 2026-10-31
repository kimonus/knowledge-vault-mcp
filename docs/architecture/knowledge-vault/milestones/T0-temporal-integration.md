# T0 — Temporal post-merge integration checkpoint

**Priority:** required gate. **Effort:** M (1–2 weeks including rollout coordination), medium estimate confidence. **Owner:** temporal implementation owner and maintainer; architecture-review agent does not implement or merge it.

## Problem and evidence

The reviewed baseline has optional validity dates and mutable `status`, but search does not reconstruct state from out-of-order evidence ([search](../../../../src/knowledge_vault/services/search.py)). A separate `feat/temporal-knowledge` branch at `ce6d35a` contains migration 0006, immutable evidence and a read-time resolver; its implementation report claims passing tests. That is work in progress relative to baseline, not deployed proof. Dependency: merge through the project's normal PR/check process with maintainer authorization.

## Scope and architectural decision

Review the branch's actual contracts and migration, reconcile with any intervening main changes, and verify it rather than creating a second temporal design. Preserve legacy clients and assertion data. Structured state identity is exact subject/property/scope/state type. The resolver, not generic search status, answers current/historical questions. No materialized current-state cache unless measured necessity. No production migration or deployment without the separate authorization required by repository policy.

## Implementation requirements and affected areas

Verify `migrations/versions/0006_temporal_evidence.py`, `domain/temporal.py`, `services/temporal.py`, ingestion, shared responses, MCP/HTTP routes, skill/policy, tests and runbooks on the final merged commit. Exercise old-client writes and reads; confirm immutable evidence keys, idempotent retries, correction-vs-transition, `as_of` vs `known_at`, unknown dates, precision overlap, conflicting values, deletion retirement and scope separation. Confirm `known_at` is documented as receipt-time approximation, not commit-visibility history. Refresh client tool schemas and routing guidance after deployment.

## Acceptance and test strategy

All repository gates in `AGENTS.md`; real disposable pgvector upgrade from 0005, backup/restore rehearsal and old-client compatibility; pure permutations and concurrent imports; authenticated MCP calls through the deployed public/private path as appropriate, including concurrent first use. Verify API logs/restarts after the calls. Record exact commit, migration, image digest and test outputs. Do not call rollout status alone deployment verification. Metrics: 100% expected temporal outcomes on gold temporal fixtures; zero order-dependent state differences; zero regression in legacy contract tests. If a gate is unavailable, record it as missing.

## Rollback and risk

Roll back application image while retaining additive schema, pause structured clients, preserve evidence and backups. Do not downgrade production 0006: that would destroy temporal evidence. Main risks are migration compatibility, stale client schemas and incorrect assistant routing; the checkpoint ends only when all three are verified.
