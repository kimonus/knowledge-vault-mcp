# TASK-0018 r3 — R-019 upgrade write-acceptance boundary

Implementation report; independent reviewer acceptance remains pending. R-011 through R-017
remain substantially accepted by the operator-reported static review; this work changes only
their remaining upgrade recovery boundary. No schema downgrade, production data, deployment,
merge or release is involved.

Input manifest: `babf6ddf3870283a91a373bac686708a80088764`.
Input artifact commit: `9f04c860a8916c7bfd4534535b23b5940a28290a`.
Reviewed patch: `608ee0b3c41897a9f4f509b6319f4b10874ad306983fb10fb99838bfa1776bfd`.
Before edits all 468 files/symlink targets and the complete application patch matched r2.
No unrelated operator changes were present.

## Finding disposition and implementation

**R-019 — FIXED (implementer disposition; independent acceptance pending).**

Source entry points: `deploy/standalone/docker.py:351` (runtime), `:362` (durable migration
success), `:368` (gateway guard), `:455` (versioned upgrade journal), `:549` (forward/legacy
recovery). The existing `phase`/`save`/`atomic_private` durability primitives remain unchanged.

`deploy/standalone/docker.py::Instance.start_runtime` persists `migration_completed: true`
through the existing fsynced journal after a successful migration reply, before any gateway
startup. This is a recovery commit, not a readiness claim. The gateway guard requires this
marker for upgrades. API readiness, fresh worker heartbeat and authenticated TLS verification
remain necessary for final completion.

`Instance.recover` uses that marker to recover strictly forwards with the same candidate image
and database, without backup/restore or migration reruns. Any acknowledged candidate writes
therefore survive controller death after gateway subprocess completion, during verification,
or before the final ready-state save. Failure stops writers and keeps the forward-only marker.

`Instance.upgrade` records `upgrade_recovery: forward-v1` before side effects. Under this protocol,
an ambiguous/lost migration reply without the completion marker implies no gateway exposure;
the existing conservative fresh-target recovery advice remains appropriate. An interrupted r2
journal lacks that guarantee, so the new controller refuses rollback advice and preserves the
candidate database for operator-led forward repair.

## Regression evidence

- `tests/unit/test_upgrade_commit_boundary.py`: journal-before-subprocess order, lost migration
  reply, gateway guard, post-migration forward recovery, failed forward retry and legacy refusal.
- `tests/unit/test_standalone_interruption.py`: real SIGKILL at the new migration-complete phase,
  and forward recovery after pre-completion verification; earlier migration-failure tests remain.
- `tests/integration/test_standalone_upgrade_boundary.py`: exact captured r2 controller reproduces
  an acknowledged TLS/MCP write missing after its recommended pre-upgrade snapshot restore;
  r3 refuses that legacy advice. R3 is interrupted before gateway startup, after its real Docker
  restart reply, after successful authenticated verification, and after a successful migration
  reply before the journal save. Every acknowledged write is read back after recovery; the
  pre-exposure cases establish that the listener could not accept a write.
- Existing managed lifecycle still exercises successful upgrade, genuinely failed migration,
  correction replay, overlap/interrupted retirement, fresh-target restore and real-model offline
  embedding/concurrent search.

Exact final commands, native logs, per-boundary evidence, separate coverage and input-equivalence
proof for any reused checks are recorded in the r3 coordination handoff. This document does not
substitute static reasoning for real Docker execution results.

Final execution: **856 server tests passed**, four opt-in Docker tests covered in the separate
**89-test controller/Docker gate**. Server coverage: **97.96% statements / 92.94% branches**.
Current-controller coverage: **94.05% statements / 86.41% branches**; the copied r2 controller is
explicitly excluded from this measurement. Builds, backup/restore/retention, image/source security,
Helm/Cloudflare/Dashboard, lockfile, formatting, lint, typing and contract drift passed. All these
gates ran for r3; no r2 runtime evidence is represented as a new result or reused to replace them.

The real r2 reproduction confirms the acknowledged post-gateway assertion is missing after the
recommended old snapshot restore. For r3, the gateway-return and post-verification cases each
acknowledge a distinct new assertion, then SIGKILL; both survive forward recovery on the same
database. The before-gateway case has no listener. The migration-return-before-marker case has
no listener and preserves all three earlier acknowledged assertions through conservative fresh
restore. Existing successful upgrade, failed migration, rotation/retirement and correction replay
also passed. Machine-readable boundary evidence and exact source hashes accompany the handoff.

## Recovery guarantees and limits

See [updated runbook](../runbooks/docker-standalone.md). Snapshot recovery is not recommended once
candidate writes might have been accepted. After the durable marker, recovery keeps the database
even if verification cannot finish; repair forwards. An unacknowledged interrupted client request
may have committed and must use idempotent retry. No automatic downgrade or controller-generated
production writes are added. Durability still assumes storage honoring file/directory fsync and
a trusted local Docker Engine. Physical power-loss, Podman, macOS, arm64, rootless/remote engines
remain deferred; only exercised Linux amd64 Docker is claimed.

Next milestone: independent ChatGPT re-review of the immutable r3 snapshot. Previous r1/r2 source,
reports, manifests and result history remain immutable and are not relabeled accepted here.
