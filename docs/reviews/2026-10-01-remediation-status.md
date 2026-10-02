# Remediation status for the 2026-10-01 review

Status of each finding in the
[independent solution review](2026-10-01-independent-solution-review.md) after the remediation
work done on top of commit `1125a87`.

The changes were committed and then deployed to the maintainer's homelab on 2026-10-01, after a
database dump whose restore and migration had been rehearsed on a disposable copy. The
NetworkPolicies remain unvalidated on an enforcing CNI by the maintainer's decision, so KV-014
stays open as recorded below.

## Operator actions before the next deployment

1. **Set `cloudflareAccess.publicHosts`** (and, recommended, `privateHosts`) in your private
   values. The chart now refuses to render with Access enabled and no published hostname, and the
   service refuses to start.
2. **If you use the chart's ingress**, set `ingress.className` (there is no default any more).
   The ingress now routes only `/mcp`, `/api/v1`, and `/.well-known`. If you use local images,
   set `image.repository`; the chart default is now the published GHCR image.
3. **Provide the embedding model locally** (`modelCache.existingClaim`) or keep embeddings
   disabled. Pods no longer attempt to download weights.
4. **Rebuild and redeploy both API and worker from one image digest** (KV-017). The upgrade
   applies revision `0002_enumerated_value_checks`; new Pods wait for it.
5. **Protect release tags.** Add a tag ruleset for `v*` on GitHub, then push the first tag to
   exercise the release workflow, which has still never run.
6. **Check the backup history.** If the CronJob was enabled, earlier runs are likely to have
   failed (KV-001). After upgrading, confirm that a snapshot exists and run the restore exercise
   in [backup-restore.md](../runbooks/backup-restore.md). The first successful run also prunes
   snapshots that older versions left behind under per-run paths.

## Findings

| ID | Severity | Status | What changed | How it is verified |
|---|---|---|---|---|
| KV-001 | High | Fixed | `scripts/backup.sh` keeps the restic cache on the writable scratch volume; the chart also sets `RESTIC_CACHE_DIR` | `scripts/ci/test_backup_restore.sh` now runs the job with `--read-only`; passed on an image built from this tree |
| KV-002 | Medium | Fixed | Snapshots use a stable `--host`; `forget` is applied to the snapshot tag as one group | The smoke test runs three backups with keep=1 and requires exactly one snapshot |
| KV-003 | Medium | Fixed, with an opt-in stricter mode | Host names are canonicalized (case, port, trailing dot); a missing or repeated `Host` is refused; published hostnames are an explicit, validated setting; `privateHosts` turns the rule into an allowlist | `tests/unit/test_cloudflare_auth.py`, `tests/contract/test_http_stack.py::test_public_hostname_always_requires_an_assertion` (both modes, including raw duplicate-Host and HTTP/1.0 requests) |
| KV-004 | Medium | Fixed | `append_knowledge` passes items to the service unvalidated while still advertising the item schema and `maxItems` | `test_mcp_flush_rejects_items_individually_and_explains_errors` over real Streamable HTTP |
| KV-005 | Medium | Fixed | Domain errors become `code: message` tool errors; services raise `invalid_request` instead of `ValueError`; offset-less timestamps are UTC; streamed oversize bodies return 413 | Same test, plus `test_http_client_errors_are_problem_details_not_server_errors` |
| KV-006 | Medium | Fixed | `hide_parameters=True`; NUL is rejected per item; unhandled errors are caught at the application boundary and logged as type, SQLSTATE, and code locations; stdlib and uvicorn records go through the JSON redactor; access logging is replaced by the structured request log | `test_logs_never_contain_assertion_text_or_exception_messages`; a real server run produced only JSON log lines |
| KV-007 | Medium | Fixed for the listed shapes; inherent limits remain | 18 patterns, applied after NFKC folding and removal of invisible characters, to content, topics, and all source fields; URL userinfo is refused | `tests/unit/test_domain.py` (23 rejected shapes, 5 allowed references, 8 field cases) |
| KV-008 | Medium | Fixed | A correction to already-known wording supersedes the target and revives the existing row; `superseded` reflects real changes; uncertain/disputed → current on re-affirmation; forget preview reports hidden predecessors | `test_correction_back_to_known_wording_supersedes_and_revives`, `test_reaffirmation_upgrades_uncertain_status…`, `test_forgetting_cascades_and_reports_hidden_predecessors` |
| KV-009 | Medium | Fixed | The worker runs expiry and purge on an interval; commit and append record expiry durably; confirmation tokens are purged | `test_expired_batches_cannot_commit_and_staging_is_purged`, `test_worker_loop_runs_maintenance…`; a real worker run expired an abandoned batch |
| KV-010 | Medium | Fixed | Claim leases with reclaim; per-item fallback; claim and search filter by configured model; rebuild is an upsert | `test_worker_reclaims_claims_left_by_a_killed_worker`, `test_one_unembeddable_item_does_not_fail_its_batch`, `test_rebuild_is_repeatable_and_models_do_not_mix` |
| KV-011 | Medium | Fixed | Local-files-only loading, load failure back-off, `degraded` readiness, `HF_HOME`/offline env in the chart, documented cache population | Built image, no network: first attempt 6 s (import time), later attempts 0.00 s, versus about 70 s per call before; unit test for back-off |
| KV-012 | Medium | Fixed | `sources` on assertion views, `fetch` metadata, and search results | `test_sources_round_trip_through_retrieval` and the MCP contract test |
| KV-013 | Medium | Fixed | `0001_initial` is explicit DDL (schema dump identical to the previous revision's output); `0002` adds enum CHECKs; init containers wait for `(head)` | Schema diff of old vs new 0001: identical; `test_migrations_downgrade_and_upgrade_through_every_revision`; `test_database_rejects_values_outside_the_enumerations` |
| KV-014 | Medium | Fixed in the chart; **not validated on an enforcing CNI** | One policy per component; PostgreSQL ingress from application Pods only; JWKS and backup egress modelled | Rendered and inspected in five configurations; kubeconform valid. A disposable cluster on the development host was not created because that host also runs the live cluster and shares kernel limits with it |
| KV-015 | Medium | Fixed | One rate guard shared by MCP and HTTP; `Retry-After`; JWKS refresh floor of 30 s. Throttling of invalid bearer tokens is deliberately not added (see below) | `test_rate_limits_apply_to_mcp_and_http`, `test_jwks_refresh_is_throttled_for_unknown_key_ids` |
| KV-016 | Medium | Fixed | Runbook commands, Secret and resource names, the MCP example, readiness and architecture statements corrected; the generator accepts the documented flag spellings; a single record is a valid bootstrap value | `tests/unit/test_operator_scripts.py` runs the documented command and loads its output |
| KV-017 | Medium | Fixed in the homelab | API and worker were redeployed from one image built from the remediated tree; revision `0002_enumerated_value_checks` was applied | Both Pods report the same image ID with PyJWT 2.15.1; the database is at the new revision with its row count unchanged; anonymous requests get `401` on both hostnames, including Host variants of the published name |
| KV-018 | Low | Fixed | Bounded route labels; tool-call, search-latency, and pool metrics recorded; worker metrics port and liveness probe; Access rejections logged with a request ID; readiness reports degraded embeddings; `otel_enabled` now emits content-free request spans and fails closed without the SDK; the chart ingress routes only client-facing paths, so `/metrics`, `/health/*`, and the OpenAPI document are cluster-internal | `test_metrics_are_recorded_and_route_labels_are_bounded`, `test_request_spans_are_content_free_and_optional`, rendered ingress paths |
| KV-019 | Low | Fixed | `Origin` on `/mcp` must be absent or listed in `corsOrigins` | `test_mcp_endpoint_rejects_foreign_origins` |
| KV-020 | Low | Fixed | Scheduling fields render; values schema is typed and closed; init containers have resources; `ingress.className` is required; default images are the published GHCR names; Secret and claim names are quoted so names such as `y` or `on` render correctly | CI renders the optional components and rejects a misspelled key |
| KV-021 | Low | Fixed | OAuth protected-resource metadata is served, and referenced from `401` challenges, only for published hostnames; elsewhere the challenge is a plain `Bearer` | `test_mcp_endpoint_rejects_foreign_origins`, `test_public_hostname_always_requires_an_assertion` |
| KV-022 | Low | Fixed | 162 tests (was 59), including concurrency and real-transport tests; tests no longer read `.env`; coverage 98.2% statements / 91.3% branches | Coverage gate |
| KV-023 | Low | Partly fixed | Unused dependencies removed; CI renders more chart configurations; the release workflow scans the pushed digests and no longer publishes `latest`; a test asserts that the pgvector pin and the project version agree in every file | `tests/unit/test_repository_consistency.py`. **Open — operator action:** the release workflow has never run (needs a tag push) and there is no tag ruleset (a GitHub setting) |
| KV-024 | Low | Fixed, with one documented limit | `replayed` is correct; identical-content races converge; superseding resolves conflicts; possible conflicts are detected without shared topics; `environment` is validated; `maxItems` advertised; the token generator takes the pepper only from a file or standard input | `test_conflicts_are_detected_without_shared_topics`, `tests/unit/test_operator_scripts.py`. **Limit:** paging can repeat rows while the corpus changes (documented in the tool reference) |

## Follow-up from the automated cloud review

A cloud review of the working tree ran after the remediation. Two of its findings were valid and
are fixed:

- `commit` retried every `IntegrityError` and reported it as a write race. It now retries only
  the content-hash collision that a rerun resolves; repeated source URLs within one assertion,
  which triggered a deterministic violation, are collapsed to one provenance row.
- The provider's `degraded` state covered only a failed model load. It now also reports a model
  that loaded but whose most recent embedding call failed.

Its other three findings reported `auth/hosts.py`, `worker/runner.py`, and the enum CHECK
constraints as missing. Those exist as new, not yet tracked files
(`migrations/versions/0002_enumerated_value_checks.py` holds the constraints); the review bundle
contained only changes to tracked files. **Remember to `git add` the new files when committing.**

## Consistency follow-up (2026-10-02)

Work done after the review items were closed, to remove the differences between what the project
describes, tests, and runs:

- **One deployment definition.** The chart can now express a Traefik and host-path deployment
  (`internalPostgresql.existingClaim`, host-directory backups, `extraEnv`, `extraObjects`), with
  `values-traefik-hostpath.yaml` as a complete example rendered in CI.
- **Failures are visible.** Worker and backup heartbeats, operational statistics, and the
  optional watchdog CronJob with webhook notification
  ([monitoring runbook](../runbooks/monitoring.md)).
- **One set of response shapes.** The HTTP API and the MCP tools return the same models
  (`domain/responses.py`); a contract test compares them.
- **Release rehearsal.** The release workflow can be run manually without publishing.
- **Conventions.** `AGENTS.md` records the branch, worktree, and single-definition rules.

Still open: KV-014 (NetworkPolicies on an enforcing CNI) and the first tagged release.

## Deliberately unchanged

- Invalid bearer tokens are not throttled. Tokens carry 256 bits of entropy, each attempt costs
  one HMAC, and the only available key—the client address—is the ingress or tunnel proxy, so a
  throttle would mostly affect legitimate clients.
- Tracing export needs `opentelemetry-sdk` and the OTLP exporter in the image. They are not in
  the locked dependency set because the SDK depends on a pre-release package, which the
  project's dependency policy excludes.

- Re-submitting the exact wording of a superseded assertion **without** `supersedes_id` confirms
  it but leaves it superseded. Reviving it requires an explicit correction.
- Forgetting a correction does not revive the assertion it replaced; the preview reports it.
- No approximate vector index and no generic (non-Cloudflare) JWT adapter were added.

## Verification after remediation

Run on the working tree on 2026-10-01 (Python 3.12.14, uv 0.12.21, Docker 29.1.3).

| Check | Outcome | Notes |
|---|---|---|
| `uv lock --check`, `uv sync --locked` | passed | 127 packages |
| `ruff format --check`, `ruff check`, `pyright` | passed | 0 errors |
| `pytest tests/unit tests/property tests/contract` | passed | 139 |
| `pytest tests/integration tests/e2e` | passed | 23, real pgvector container |
| Coverage gate | passed | 98.2% statements, 91.3% branches |
| `pip-audit`, `bandit` | passed | |
| `gitleaks git`; `gitleaks dir` on tracked and new files | passed | A scan of the whole working directory also reports ignored local files (`.env`, caches) |
| `hadolint`, `actionlint` | passed | |
| `docker build` (application, backup) | passed | |
| `scripts/ci/test_backup_restore.sh` | passed | read-only root filesystem, retention asserted |
| `trivy image` (application, backup) | passed | no fixable HIGH or CRITICAL findings |
| `syft` SBOM | passed | |
| `helm lint`, `helm template`, `kubeconform` | passed | default 14 resources; Cloudflare 18; external PostgreSQL with backup and scheduling 14 |
| Alembic upgrade, per-revision downgrade/upgrade, `alembic check` | passed | |
| MCP Inspector 2.4.0 `tools/list` against the real entry module | passed | 12 tools |
| Markdown local links, YAML/JSON parse, `git diff --check` | passed | |
| NetworkPolicies on an enforcing CNI | not run | would need a second cluster on the host that runs the live one |
| Release workflow | not run | requires a tag push |
| Codex plugin validator | not run | validator not installed |
| Homelab deployment | passed | migration Job completed; API and worker rolled out with no restarts; logs are JSON only |
