# Standalone portability audit — Phase 0

Status: draft; independent architecture/security review completed, PR checks pending. Evidence is static inspection at
bootstrap head `2aaa4e46bf59266938319f65a3638f26b9740858`, over main baseline
`39b6af7c27950a7e57159b59cc7157ff8728f248`; it is not runtime validation. The original brief,
AGENTS.md, architecture and ADRs remain controlling. [Proposed ADR 0009](../decisions/0009-standalone-portability.md)
records the boundary and unresolved transport choice.

## Inventory and classification

Classifications describe work needed for standalone, not proof of support. Paths link to inspected
source at this branch; tests establish existing behavior only where actually executed.

| Area | Evidence and observed behavior | Classification / action |
| --- | --- | --- |
| Core and persistence | [architecture](../architecture.md), [ADR 0004](../decisions/0004-postgresql-worker-queue.md), [ADR 0007](../decisions/0007-temporal-evidence.md): shared services, PostgreSQL/pgvector, persistent queue and temporal evidence. | Verified by inspection / no core redesign; retain contracts and database constraints. |
| Startup and config | [API entry](../../src/knowledge_vault/main.py) binds container `0.0.0.0:8000`; [config](../../src/knowledge_vault/config.py) accepts environment settings; [API deployment](../../charts/knowledge-vault/templates/deployment-api.yaml) injects ConfigMap and Secret values. | Deployment abstraction: container bind is not a host publishing policy. Supply standalone secret/config inputs and loopback-only host publishing. |
| Authentication and origin | [app](../../src/knowledge_vault/api/app.py), [middleware](../../src/knowledge_vault/api/middleware.py), [device tokens](../../src/knowledge_vault/auth/device_tokens.py); MCP Origin check exists; Host allowlisting is installed through Cloudflare middleware. | Config/refactor: enforce suitable standalone Host validation independently of Cloudflare; preserve scope guard and origin tests. No anonymous mode. |
| Migrations | [migration Job](../../charts/knowledge-vault/templates/job-migration.yaml) runs `alembic upgrade head`; [helpers](../../charts/knowledge-vault/templates/_helpers.tpl) gate service startup on migration revision. | Deployment abstraction: explicit one-shot migration and readiness gate; test restart, failure, serialization and restore. |
| Worker and maintenance | [worker entry](../../src/knowledge_vault/worker/main.py), [loop](../../src/knowledge_vault/worker/runner.py), [jobs](../../src/knowledge_vault/worker/jobs.py): staging expiration and purge, orphan artifact, confirmation-token and maintenance-event purge and heartbeat share the embedding loop; provider and worker are constructed unconditionally. | Config/refactor: prove maintenance without inference in lite mode, without claiming or exhausting existing pending embedding jobs; preserve leases and safe shutdown. |
| Model cache | [providers](../../src/knowledge_vault/embeddings/providers.py), [config](../../src/knowledge_vault/config.py), [tables](../../src/knowledge_vault/persistence/tables.py), [worker deployment](../../charts/knowledge-vault/templates/deployment-worker.yaml): download defaults off; cache is read-only; vectors are fixed at 384 dimensions. Provider loads lazily and serializes load and encode per process; API and worker may each hold a copy. | Deployment abstraction: provision pinned local model separately; verify cache permissions, dimension compatibility, memory, cold start and network-denied native inference. |
| Networking | [Service](../../charts/knowledge-vault/templates/service.yaml), [network policy](../../charts/knowledge-vault/templates/networkpolicy.yaml), [ingress](../../charts/knowledge-vault/templates/ingress.yaml), [ADR 0005](../decisions/0005-private-connectivity.md): cluster-private origin and TLS edge are deployment controls. | Deployment abstraction plus unresolved architecture boundary: map private networks and host binding; authenticated TLS remains required. |
| Health and metrics | [app](../../src/knowledge_vault/api/app.py), [API deployment](../../charts/knowledge-vault/templates/deployment-api.yaml), [worker deployment](../../charts/knowledge-vault/templates/deployment-worker.yaml): API health and `/metrics` share listener; worker metrics are optional. | Deployment abstraction/refactor: prevent metrics exposure on host listener while preserving health checks; heartbeat freshness is needed beyond process liveness. |
| Backup and recovery | [backup image](../../Dockerfile.backup), [backup script](../../scripts/backup.sh), [backup CronJob](../../charts/knowledge-vault/templates/backup.yaml), [backup runbook](../runbooks/backup-restore.md): pg_dump and restic use Kubernetes-provisioned secrets/storage. | Deployment abstraction: independently provision encrypted repository and secret inputs; restore into fresh isolated DB and verify data and failed-upgrade recovery. |
| Shutdown | [worker loop](../../src/knowledge_vault/worker/runner.py) releases claims on exit, with lease expiry fallback; [worker](../../src/knowledge_vault/worker/main.py) closes database; [API](../../src/knowledge_vault/api/app.py) has lifespan handling. | Verified by inspection / validate stop time and interrupted work on each runtime. |
| Images and supply chain | [app image](../../Dockerfile), [backup image](../../Dockerfile.backup), [release](../../.github/workflows/release.yml): pinned stages; release builds `linux/amd64`, scans, signs and emits SBOMs. | Deployment abstraction: prove native arm64 wheels and inference, and retain per-platform scan/signature/provenance guarantees before any approved publication. |
| Existing verification | [CI](../../.github/workflows/ci.yml), [contract tests](../../tests/contract), [integration tests](../../tests/integration), [E2E](../../tests/e2e), [backup test](../../scripts/ci/test_backup_restore.sh). | Existing gates are necessary, not evidence of Compose, Podman, macOS, native arm64 or external reachability. |

## Risk register and plan delta

| Risk | Mitigation / acceptance evidence | Phase |
| --- | --- | --- |
| Plain loopback HTTP conflicts with mandatory authenticated TLS; local processes/browser and VM forwarding expand trust boundary. | Default to authenticated TLS. Owner must explicitly decide any plaintext exception after security review; no implementation relying on it before that decision. | 0, 3 |
| Standalone Host is not independently constrained when Cloudflare is off; DNS rebinding and bypass are possible. | Add strict explicit allowlist with missing/repeated/invalid Host negative tests and MCP Origin tests; verify actual path. | 3 |
| API metrics share published listener and leak operational information. | Filter or isolate metrics without breaking health or auth; verify from host and second endpoint. | 2, 3 |
| Lexical-only mode still runs embedding worker code, threatening maintenance without weights. | Separate scheduling from inference with bounded tests and verify expiration/purge and heartbeat with model unavailable. | 2, 4 |
| Migration races, partial upgrade or bad rollback cause data loss. | One migration owner, revision gate, backups, restart and failure tests; restore a fresh instance, no assumed downgrade. | 2, 5 |
| ARM dependencies or emulation falsely imply native support; release attestation coverage regresses. | Inspect wheels and build metadata; run native smoke/inference per architecture, scan and sign immutable digests; no release without owner authorization. | 1, 6 |
| Local secrets, database or model volumes leak or collide with existing instances. | Isolated project/volumes and secure operator secret store, independent credentials, no host DB port, test permissions. | 2, 3, 5 |
| macOS VM networking may expose loopback unexpectedly; corporate hardware is out of scope without authorization. | Actual second-endpoint negative tests on separately authorized real targets; report unverified otherwise. | 3, 6 |
| Offline claim fails on cold cache or model mismatch; maintenance stalls. | Pin revision and provenance; deny network after provisioning and run cold concurrent-first-use, search, ingestion and cleanup. | 4, 6 |
| Backup exists but is unusable, retention prunes unrelated data, or shared production resources are touched. | Instance-isolated encrypted repository/tags and writable scratch, preserve backup heartbeat; restore verification of assertions, artifacts, provenance and temporal state; no live credentials. | 5 |

Phase 1 remains downstream of completion of this independent Phase 0 gate. Phase 2 must include
an explicit revision gate and maintenance-only behavior; Phase 3 must address Host enforcement,
metrics routing and the TLS decision. The remaining phase acceptance criteria stay as written in
the [roadmap](../roadmaps/standalone-deployment.md). Independent static review identified origin-side
authentication and container entrypoint requirements, incorporated in ADR 0009. Runtime evidence
and any owner decision on a proposed plaintext exception are not yet complete.

Acceptance tests must include missing, repeated and untrusted Host; disallowed Origin; anonymous
and wrong-scope MCP; second-endpoint access to API, metrics, database and worker; failed, unfinished
and concurrent migration readiness; preexisting pending jobs in lite mode, all cleanup duties and
heartbeat; SIGTERM claim release; actual offline inference; restore of artifacts, provenance and
temporal data; and proof that native runs are not emulated. These are planned tests, not results.
