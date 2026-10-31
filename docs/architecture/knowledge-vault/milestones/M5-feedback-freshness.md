# M5 — Feedback and freshness policy

**Priority:** conditional. **Effort:** M (4–10 days), low-medium confidence. **Dependencies:** M1 and preferably M2/M3. **Owner:** client integration/observability agent.

## Problem and evidence

Search returns counted assertions, but a retrieval is not a use; explicit uses were zero in the live snapshot ([maintenance semantics](../../../../docs/knowledge-maintenance.md)). `review_after` exists, but no due reviews were reported. There is no baseline of answer correctness or stale-knowledge harm. Broad usage telemetry would add privacy cost without proving usefulness.

## Scope and architectural decision

Add an opt-in feedback workflow that lets a user or evaluator mark an answer case as correct, wrong retrieval, missed extraction, stale evidence or unsupported synthesis. Store only bounded event IDs, category codes, timestamps and assertion IDs when genuinely needed; no raw query, answer, transcript or assertion content in metrics/logs. Turn confirmed failures into redacted regression fixtures or operator-local private cases. Use per-kind review guidance: durable preference on explicit correction, active configuration on observed change, volatile external fact at a stated `review_after`, historical evidence retained indefinitely. No global TTL or automatic deletion.

## Implementation requirements

Start with client-side feedback and the existing idempotent `report_knowledge_usage` for actual use. Add server event receipts only if client-only feedback cannot support the agreed measurement; any new API field belongs in shared domain responses and any persistence in a new Alembic revision. Fixed-label metrics may count feedback categories and review outcomes; omit caller-supplied IDs/paths. Define a small human review queue for stale or conflicting records and a safe way to mark review completed without claiming source verification absent evidence.

## Acceptance and testing

At least one synthetic end-to-end correction produces a labeled failure, a regression case and a passing re-evaluation. Feedback retries are idempotent; a search return alone never increments actual use. Review policies distinguish durable preference from time-sensitive price and do not delete either. Tests cover secret/prompt-injection-shaped feedback, deletion cascade/retention, authorization and privacy of logs/metrics. Report feedback participation and precision of due-review suggestions; do not interpret low participation as high quality.

## Alternatives, migration, risk and rollback

If M1/M2 show no practical feedback workflow, defer this milestone. The alternative of sampling full queries/answers is rejected for privacy and storage burden. Disable optional tracking and revert client policy on regression; keep content-free receipts until retention expiry. Additive schema, if any, remains after code rollback until safely retired.
