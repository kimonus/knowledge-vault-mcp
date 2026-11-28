# TASK-0018: independent review remediation

Implementation agent report; independent acceptance belongs to the reviewer. The requested
milestone is a new review snapshot, without application commits, publication, merge or deployment.

Base: `698811896dd294ed321fca78c905b3ad14b7d773`.
Reviewed patch: `355fd28d5e97229e5c256ff1eeb4a20a042851eaf57f4d929bdf067e84a209de`.
Before editing, all 462 captured source files and the regenerated complete patch matched the
reviewed handoff. No untracked implementation files or unrelated operator changes existed.

## Remediation matrix

| Finding | Implementation disposition | Exact source entry points | Regression evidence |
| --- | --- | --- | --- |
| R-011 | FIXED | `deploy/standalone/docker.py:198` (`quiesce`), `:448` (`upgrade`) | `test_upgrade_recovery_never_assumes_partial_migration_completed`; managed Docker E2E acknowledges a synthetic MCP write immediately before quiescence, injects a partial migration and verifies that write after fresh-target restore. Backup failure never enters migration. |
| R-012 | FIXED | `deploy/standalone/docker.py:67` (file/directory durability), `:173` (journal), `:282` (allocation intents), `:538` (`recover`) | SIGKILL at six upgrade phases, durable-state reload, ordinary-start refusal, forward retry before migration and conservative refusal after possible migration; fsync ordering and failed-durability regression. |
| R-013 | FIXED | `deploy/standalone/docker.py:91` (`lifecycle_lock`), `:904` (`main`) | Deterministic second-process lock holder writes a newer config; competing CLI refuses before constructing an instance; next lock holder reads the new generation. Init/restore exclusively create and lock before provisioning. |
| R-014 | FIXED | `src/knowledge_vault/embeddings/manifest.py:18`, `src/knowledge_vault/embeddings/providers.py:59`, `deploy/standalone/docker.py:335`, `deploy/standalone/compose.full.yaml:7` | Exact inventory/digest/dimension checks, altered weights, changed manifest, unsafe links/paths, incomplete binding and mutation during model construction. Full offline E2E uses manifest identity for worker and retrieval. |
| R-015 | FIXED | `deploy/standalone/docker.py:477` (`rotate`), `:646` (sealed generation), `:701` (generation creation), `deploy/standalone/volume-init.py` (seed fsync) | SIGKILL for overlap and retirement at 16 boundaries each: every file/seal, generation/volume preparation, before/after selection rename, selection and acceptance. Real Docker retirement interruption resumes the selected generation; retired token remains refused and correction replay survives rotation/restore. |
| R-016 | FIXED | `src/knowledge_vault/persistence/schema_guard.py:35` (`ready`) | Shared bounded check, no later positive cache; 32 overlapping unit callers and 64 real PostgreSQL callers, mismatch/unavailable database/timeout/cancellation and recovery tests. |
| R-017 | FIXED | `deploy/standalone/docker.py:351` (restart boundary), `:371` (read-only database clock), `:396` (fresh heartbeat), `deploy/standalone/compose.docker.yaml` | Stale/pre-boundary heartbeats fail; lifecycle requires fresh successful maintenance after restart. Full offline E2E waits for an actual synthetic embedding job and checks concurrent API inference. |

The journal stores nonsecret recovery metadata; secret material remains in private immutable
generation directories and UID-scoped volumes. A single config replacement selects host generation
and volume together. Recovery never chooses retired generations. Correction-receipt HMAC pepper
is preserved. No domain schema, MCP contract or Kubernetes identity architecture changes were made.

## Verification record

Final commands, native outputs, coverage JSON, security/SBOM results and the complete source
inventory are captured in the new TASK-0018 coordination handoff. Server-package coverage and
deployment-controller coverage are reported separately; one is never substituted for the other.
Earlier completion measurements in `standalone-docker-completion.md` are historical, not evidence
for this remediated snapshot. A local handoff is not independently accepted or published.

R2 execution: **847 server tests passed**, two opt-in Docker tests skipped in that run and
covered by the separate **78-test controller/Docker gate**, including lexical and
real-model offline profiles. Server coverage is **97.96% statements / 92.94% branches**; controller
coverage is **93.88% statements / 86.00% branches** with no deployment-controller exclusions.
Image builds, backup/restore/retention, security, Helm/Cloudflare/Dashboard, lockfile, formatting,
lint, typing and contract-drift checks passed. Exact native reports remain in the handoff.

**Earlier gate blocker resolved: incorrect test invariant.** On explicit operator authorization,
the compatibility analysis in [ADR 0011](../decisions/0011-normalization-hash-compatibility.md)
establishes that NFKC followed by casefold may produce decomposed output (U+1E96 → U+0068 U+0331).
The property now checks the actual persisted contract: idempotence, compatibility-equivalent
input, stable casefold and whitespace. Three fixed legacy hash vectors and a real PostgreSQL
seeded-row test verify exact matching, deduplication and commit replay. No test was excluded.
`src/knowledge_vault/domain/normalization.py` remains byte-identical to the exact application base;
no hash migration or production algorithm change occurred. R1's failed gate and initial defect
classification remain historical evidence; this explicit correction supersedes that classification.

R2 reuses the previously executed Docker, controller-coverage, image/backup/security and Helm
results only after comparing all 165 relevant runtime/build/controller-test/chart/workflow inputs
byte-for-byte with r1. They are unchanged; only normalization tests and review documentation differ.
Fresh full server, compatibility and static checks are distinguished from reused evidence in the
r2 handoff. No fresh runtime execution is claimed for reused reports.

## Operational limits and remaining work

- Verified runtime scope is Linux amd64 Docker. Podman, macOS, arm64, rootless Docker and remote
  engines remain deferred; no live cluster or production knowledge is used by these rehearsals.
- Host-restart behavior relies on the same durable journal exercised through process death and
  reloading; a physical power-loss test is not claimed. Storage must honor file/directory fsync.
- Partial migrations are never automatically rolled back. Once migration may have begun, use
  the recorded verified snapshot and prior image in a NEW instance. Upgrade downtime includes
  stopped writers, dump, full repository verification and functional acceptance.
- No automatic TLS renewal, credential-generation/anchor garbage collection or backup pruning
  is introduced. Check certificate expiry, protect retained material and inventory exact IDs.
  Scratch is a bounded 1 GiB tmpfs; full repository checks have a 1,800-second deadline and grow
  with retained data. These failure paths leave writers stopped before migration.
- A local integrity-only test manifest does not establish upstream model authenticity. Provision
  the manifest through an independently trusted process. Existing revision-only standalone full
  profiles require fresh manifest-bound restore/reembed; dimensions remain 384.
- See the handoff security summary for unfixed upstream image advisories. The release gate checks
  fixable HIGH/CRITICAL vulnerabilities; passing it does not mean every upstream advisory is absent.
- Next milestone: independent ChatGPT re-review of the exact remediated snapshot. The operator
  authorized completion and coordination publication; its immutable manifest/result identify the
  published revision. No application merge, release or live deployment is part of this milestone.
