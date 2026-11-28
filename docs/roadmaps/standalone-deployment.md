# Standalone Container Deployment Roadmap

Status: PROPOSED. Owner approval covers planning and supervised implementation, not production deployment, merges, or release publication.
Baseline: the current protected main branch; resolve and record its exact SHA before Phase 0.
Primary objective: first-class Docker Compose and Podman deployment, including a standalone instance on an Apple Silicon work Mac reachable only through the host's loopback interface.

## Non-negotiable invariants
- Preserve Knowledge Vault's single-user model, MCP/HTTP contracts, PostgreSQL 17 + pgvector, idempotent ingestion, temporal evidence, deterministic resolution, secret rejection, scope enforcement, and existing Kubernetes behavior.
- Do not introduce a server-side generative LLM. Keep client-side extraction and optional **local** embeddings; no hosted inference.
- No traffic to external services is required at runtime after images and model weights have been provisioned.
- No changes to live homelab, corporate Mac, release tags, or production secrets.
- Do not represent a platform as supported without actual runtime and E2E evidence.

## Phase 0: Architecture and portability gate (first task)
Inventory every Kubernetes assumption in startup, configuration, authentication, migrations, worker maintenance, model caching, networking, health/metrics, backup, recovery and shutdown. Distinguish core coupling from deployment-only setup. Read AGENTS.md, docs/architecture.md, relevant ADRs, Dockerfile, Dockerfile.backup, Helm templates, release workflows and tests. Record findings as verified/no change, config refactor, deployment abstraction, or architecture change. Create ADR, concrete risk register and plan delta. Escalate fundamental redesign; prefer minimal changes with clear proof.

Acceptance: independent architectural review; ADR explicitly identifies the portability boundary, migration lifecycle, background maintenance contract and acceptable local HTTP trust boundary. No coding of deployment manifests before the Phase 0 gate.

## Phase 1: Multi-platform images
Build and verify linux/amd64 and linux/arm64 application and backup images, pin dependencies, inspect Python/native wheels including torch/sentence-transformers, publish multi-arch manifests only under approved release workflow and preserve digest/signature/SBOM guarantees. Confirm native arm64 inference rather than silently accepting emulation.

Acceptance: native smoke and dependency tests on both architectures; existing amd64 release gates remain intact. Unavailable hardware is a blocker to the platform's support claim, not automatic success.

## Phase 2: Standalone Compose packaging
Create a maintained Compose Spec profile (proposed deploy/standalone/) with PostgreSQL/pgvector, API, dedicated worker and one-shot Alembic migrations. Durable instance-specific volumes; non-root and read-only filesystem where compatible; health-gated startup; graceful shutdown; worker maintenance even in lexical-only mode; safe restart without uncontrolled migrations.

Acceptance: clean setup, authenticated MCP ingestion/readback, repeated startup, forced container recreation and persistent data verification.

## Phase 3: Local-only security
Bind API host publishing explicitly to 127.0.0.1, never 0.0.0.0; do not expose PostgreSQL, worker or metrics. Disable tunnel/ingress. Preserve authentication and scoped tokens, secure token-pepper/credentials injection, Host and Origin enforcement, secret-safe logs and separate admin credentials. Verify macOS VM-port-forwarding and host network behavior on Docker Desktop and Podman Machine; a config-only check is insufficient.

Acceptance: actual unauthorized/scoped MCP tests and negative reachability checks from a second network endpoint.

## Phase 4: Runtime profiles and offline model
Provide lite (lexical search, embeddings disabled) and full (local sentence-transformer cache). Verify offline execution with network denied after provisioning. Pin model revision, record cache provenance and dimensions. Keep worker cleanup and maintenance available in both modes.

Acceptance: full ingestion/search/readback and maintenance in both profiles, cold-start and concurrent-first-use tests, full-profile model inference with network blocked.

## Phase 5: Backup, restore and upgrades
Adapt existing backup image and restic/pg_dump mechanisms, encrypted independent backup repository, immutable or verifiable backup metadata. Document safe migrations and rollback by restore (not reversible schema migrations unless specifically reviewed). Exercise restoring a *new* instance and verifying assertions, artifacts, source provenance and temporal state.

Acceptance: tested disaster recovery plus failed-upgrade recovery, no shared production credentials or volumes.

## Phase 6: Cross-engine/OS verification
Create isolated E2E matrix: Docker Linux amd64/arm64, Docker Desktop macOS Apple Silicon, Podman Linux amd64/arm64, Podman Machine macOS Apple Silicon. Include Compose config, startup/readiness, real authenticated MCP, restart, maintenance, security, backup/restore, offline embeddings, and Kubernetes regression gates. Collect host OS, engine version, image digests, logs (scrubbed), CI URLs, failure reason and proof of tests.

Acceptance: claims of support strictly match tested targets; unavailable combinations remain openly unverified.

## Phase 7: Documentation and release readiness
Standalone quickstart, macOS and Podman runbooks, security threat-model updates, ADRs, AGENTS.md and release docs. Independent reviewer validates docs on clean environment. Existing Minikube guide and release contract remain supported.

Acceptance: documented one-time setup, start/stop, update, rotate tokens, backup, restore and connect a local MCP client, all independently reproduced.

## PR dependency and sequencing
0 architecture review + ADR
1 multi-platform image compatibility (after 0)
2 Compose/runtime services (after 0 and 1)
3 local-only security (after 2)
4 embeddings/offline (after 2; can parallelize with 3)
5 backup/restore (after 2; can parallelize with 3/4)
6 E2E & matrix (after 3,4,5)
7 documentation and independent release gate (after 6)

PR numbering is logical, not necessarily GitHub's issue/PR number. No broad refactoring, datastore substitution, speculative abstractions, or simultaneous mutations of production resources.

## Definition of Done
All required deployment targets independently verified; secure localhost-only behavior demonstrated; auth and maintenance preserved; offline full and lite modes passed; data survives restart, backup/restore and upgrade; releases reproducible; Kubernetes regression checks pass; independent review completed; owner receives final evidence matrix and any unavoidable manual verification steps.
