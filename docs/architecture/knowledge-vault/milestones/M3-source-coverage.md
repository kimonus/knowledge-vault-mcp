# M3 — Source coverage and provenance

**Priority:** high only if M1 measures missed-source or auditability failures. **Effort:** M–L (1–4 weeks depending on schema), low-medium confidence. **Dependencies:** T0 and M1. **Owner:** ingestion/provenance agent; no overlap with M2 search files except contract coordination.

## Problem and evidence

The server receives assertions rather than original conversations ([brief](../../../../personal-knowledge-mcp-codex-prompt.md)); a successful commit cannot certify extraction coverage. Provenance is optional, and 75 of 80 query-selected diagnostic hits lacked both typed provenance and URL source. The live sample is biased, so corpus prevalence and impact must be measured before a migration.

## Scope and decision sequence

First improve client checklist and reporting: each available source segment gets a stable operator-visible reference, extraction outcome, and disclosed context limits. Re-evaluate M1 coverage. Only if misses or replay ambiguity persist, add a bounded, content-free source-import receipt linked to assertions or temporal evidence. A receipt records opaque source ID, source kind, source-observed time/precision when known, extraction policy version, processing state, item counts, rejection codes and idempotency key. It stores no transcript, message graph, copied page or secret. A receipt proves processing/accounting, not complete semantic extraction. Original source truth stays in the external gold set.

## Implementation requirements

Client-owned work: `plugin/knowledge-vault/skills/flush-knowledge/`, initialization guidance and golden cases. Optional server work: new Alembic revision with explicit DDL, domain input and shared response models, `services/ingestion.py`, MCP and HTTP adapters, bounded read-only receipt lookup, deletion/retention rules, backup/restore tests and runbooks. Preserve old begin/append/commit semantics and clients; optional fields only. Multiple sources may support one claim; do not overwrite a first source on exact dedup. Temporal evidence should retain each source observation per T0, with link semantics reviewed against branch's evidence key.

## Acceptance and testing

M1 source-pack claim coverage improves to an agreed target without fabricated provenance; every processed segment has an accepted/rejected/partial/unknown outcome where the client has that segment. Retry the same source and flush without duplicate receipts or claims; changed-payload key reuse is rejected. Per-item secret rejection remains independent. Test partial imports, replay after crash, correction, hard deletion (including links and derived indexes), restore, old-client parity, bounded result sizes and zero raw source text in logs/metrics. Quality report separates source-processing coverage from claim recall. Run full repository gates for any schema/API change.

## Alternatives, migration, risk and rollback

Alternative: keep only existing typed references and client checklist if M1 meets targets; this is preferred until contrary evidence. A receipt table adds privacy, retention and migration burden; use a new additive revision, no legacy backfill or invented dates. On rollback disable client receipt submission and old code can ignore table. Retain additive schema until verified retention/backup policy allows removal. Never erase historical assertions just because a source receipt is incomplete.
