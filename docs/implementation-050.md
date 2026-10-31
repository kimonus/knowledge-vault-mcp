# 0.5.0 implementation tracking

Implement knowledge maintenance, context/provenance/evidence/freshness, explicit metadata repair,
relationships, topic and duplicate curation, retrieval accounting and explicit usage reports.
Preserve existing client contracts and stored facts. Release only after full quality/security,
real PostgreSQL migration/restore, authenticated staging and release-rehearsal checks pass.

See decision 0006 for contract decisions. Final signed publication is maintainer-owned (RELEASING.md).

Acceptance cases: coherent procedures; named context; dated snapshots due for review; explicit
question resolution; lower confidence/date correction with revision checks; historical state;
normal versus maintenance reads; idempotent actual-use reporting; restart/backup/restore; legacy
upgrade/rollback; confirmed deletion cascades. Never use private assertions as public fixtures.
