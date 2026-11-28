# Standalone Docker completion evidence

Historical measurements below precede independent review CHANGES REQUESTED. Current remediation
and re-review status: [TASK-0018 r3](task-0018-r3-remediation.md); do not apply these earlier measurements
to the remediated tree.

Scope: integrate the existing standalone PR stack with main at `6988118` and complete the core MCP
Docker lifecycle. The owner directed Docker completion first and Podman verification later as part
of the same initiative. No merge, publication, live homelab deployment or real knowledge write is
part of this work. This is an implementation verification record, not an independent review.

## Implemented boundary

- Local Linux Docker controller with a private state directory, per-instance lock, recorded Engine
  identity, random Compose project and atomically allocated anonymous volumes retained by anchors.
- Loopback TLS; scoped application authentication; exact Host/MCP Origin enforcement; private
  database/API network and unpublished operational endpoints.
- Private host credentials copied through stdin into new per-service UID files; no chown of host
  files, no plaintext credentials in process arguments or Compose values.
- Explicit serialized migration, fail-closed schema checks, lexical maintenance worker and full
  offline model profile. Re-embedding checks schema compatibility and closes its connection on
  failure.
- Persistent restart/recreation; token overlap/retirement; verified backup before upgrade;
  stopped writers and recorded prior image/snapshot on failed upgrades.
- Encrypted restic backup with full-data verification and success heartbeat. A separate encrypted
  recovery sidecar preserves the correction-receipt HMAC key so retries remain exact after restore.
  New device/database/TLS credentials are generated; they are not copied from the source.
  Plaintext dump only
  in bounded private tmpfs. Restore only to freshly allocated, isolated storage with new credentials,
  transaction failure propagation and exact schema compatibility before serving.

The generic `restore-safe.sh` stays disabled. Recovery is provided by the fresh-instance controller,
not by weakening its arbitrary-target checks. The existing Kubernetes backup retains pruning;
standalone backup never prunes. Its existing smoke cleanup now selects only the ID returned by
successful container creation, so a name collision cannot select an unrelated container.

## Executed verification

- Initial managed lifecycle: **1 passed in 131.93 seconds**, including real TLS/MCP, assertions,
  artifact content, source provenance, temporal state, restart, forced recreation and recovery after
  a deliberately incompatible source schema. Source/target are synthetic and separately allocated.
- Application unit/property/contract/integration/E2E regression: **757 passed**, one opt-in lifecycle
  test skipped at collection. Coverage: **98.08% statements**, **93.03% branches**. This suite uses
  real disposable PostgreSQL/pgvector. Model downloads are not performed by tests.
- Focused worker/controller tests: **16 passed** at the recorded intermediate stage.
- Ruff formatting/lint passed; Pyright passed with zero errors before final controller-only edits.
- Both application and backup images built. The existing Kubernetes-style backup/restore smoke
  passed with a read-only root and retention pruning.
- Managed source lifecycle including actual token overlap/retirement and the upgrade command passed
  in **208.40 seconds** before the final image security/receipt-key update.
- Real-model full offline profile passed in **49.55 seconds**: worker embedding, concurrent cold API
  searches, schema-guarded reembed, maintenance heartbeat, Host/Origin/auth refusals, unpublished
  metrics, a separate-bridge reachability probe, safe logs and zero automatic restarts.
- Helm lint/template, strict kubeconform and rendered-number checks passed for default-safe,
  Minikube and Cloudflare/Tunnel cases. Dashboard chart regressions passed **30 renders**; their
  generated manifests also passed strict schema/number checks.
- `uv lock --check --offline`, Ruff format/lint, Pyright, Bandit, Hadolint, Actionlint and a clean
  tracked-source Gitleaks scan passed. Local pytest caches contain intentional detector fixtures;
  the source scan therefore used a fresh export of tracked files, matching a clean checkout.
- Trivy initially found fixed HIGH findings in the pinned Debian bases. Both Dockerfiles now pin
  patched PAM and Zstandard packages. Final application/backup scans passed with no fixed
  HIGH/CRITICAL findings under the CI filter (`--ignore-unfixed --severity HIGH,CRITICAL`).
  Sources: [Debian PAM tracking](https://security-tracker.debian.org/tracker/CVE-2025-6020),
  [Debian Zstandard tracking](https://security-tracker.debian.org/tracker/CVE-2026-124851).
- Final lifecycle after image and receipt-key fixes: **2 passed in 262.72 seconds** on Linux amd64,
  Docker 29.1.3, Compose 5.8.1. This includes exact correction replay after recovery, actual old-token
  refusal after retirement, preserved current token, explicit upgrade, fresh-target recovery and the
  real-model offline/concurrent-search/network checks. API logs and zero automatic restart counts
  were checked after the authenticated client calls. Final backup-image smoke also passed again.
- Final tested source-build images before test cleanup:
  application `sha256:b5a47e1f2761d94ee8258b7144f6e8f76a5685f6bafff683629d7489ce35837d`;
  backup `sha256:70dcf86fdf10a4c78e7097032eed94be0beb24690b5943a97795e83459f4d95a`.
  These identify local builds, not signed published manifests.
- Cleanup verified and removed **73 stopped test containers/volume anchors**, **22 test networks**
  and the exact recorded review-built image IDs. Synthetic host-side evidence remains private;
  no live homelab workload, production data, registry or release was changed.

## Deferred platform and operator verification

Podman Linux/Machine, macOS/Docker Desktop and rootless Docker need their own provider, UID/mount,
forwarding, security and full recovery E2E evidence. A prior owner-reported Mac image inference smoke
does not establish full deployment support. Native arm64 deployment and signed multi-platform
publication remain separate. A different-bridge Docker probe does not prove physical LAN isolation.
An encrypted local repository does not prove independent storage survives host loss; operators must
retain an independently recoverable restic key and test their real backup medium.

The standalone package covers core MCP/API, not the optional Dashboard. Kubernetes deployment and
its real-provider authentication verification remain independent and unchanged.
