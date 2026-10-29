# 0.5.0 maintenance verification

The change addresses missing context/provenance, ambiguous evidence and lifecycle metadata,
metadata repair, procedure relationships, freshness/curation workflows and access/actual-use
accounting. No production assertions are migrated to inferred evidence or rewritten automatically.

## Executed verification

- Full regression suite: 216 passing tests before adding the previous-image regression below;
  statement coverage 98.02%, branch coverage 92.97% (required 90%/85%).
- Actual prior published application image ran successfully against the expanded schema and
  ingested/retrieved synthetic assertions; logical legacy writers also retained server defaults.
- Upgrade from 0004 with legacy rows, Alembic model agreement, pg_dump restore into a second
  disposable database, and destructive downgrade/re-upgrade on synthetic data passed.
- Both candidate images built. Backup-image retention/restore test passed. Dependency audit passed
  after applying the msgpack security floor; both image scans passed the existing fixed HIGH/CRITICAL
  gate with no reported secrets. SBOM generated for the application candidate.
- Source-only secret scan, Ruff, Pyright, Bandit, pre-commit and skill validation passed. Test-cache
  secret fixtures are excluded by scanning a clean source snapshot, matching CI's fresh checkout.
- Chart lint/render and strict Kubernetes validation: 60 standard resources passed across four
  configurations; Traefik render passed 20 standard resources with its one documented CRD-schema
  exception. Integer rendering checks passed.
- Authenticated MCP acceptance through TLS Traefik ingress passed on an isolated Minikube release
  using the existing model cache read-only. Flush/repair/relations/search/review/counters and actual
  usage were exercised. Local weights loaded without downloads. API restart retained the synthetic
  data, then confirmed forgetting cleaned it. All staging resources were removed.
- The immediate restart probe needed one initialization retry while the old pod was terminating;
  fresh initialization succeeded. No application crash restart or data loss was observed. Stateful
  MCP sessions must be reinitialized after restart; Pod readiness alone is not client verification.
- A 2,000-row synthetic read benchmark (200 reads/mode, concurrency 10) measured p95 60.51 ms with
  accounting disabled and 69.62 ms enabled, after replacing per-record writes with bulk upserts.
  This is one host measurement, not a universal latency guarantee.

All assertions/credentials used in tests and staging were synthetic. The signed release tag,
publication attestations and production promotion remain separate steps under RELEASING.md.
Required GitHub CI and exact-main-commit release rehearsal results belong to the PR/release record;
this document does not claim they ran before they actually pass.
