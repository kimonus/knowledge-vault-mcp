# Codex implementation prompt: Personal Knowledge MCP

> **Historical implementation brief.** The current architecture and security decisions in
> `AGENTS.md` supersede this document where they differ, including the use of Cloudflare Tunnel
> and Cloudflare Access for the hosted-client endpoint.

You are the principal engineer responsible for implementing a production-grade, single-user personal knowledge platform. Work directly in the current repository. If it is empty, initialize the project structure. First inspect all existing files and `AGENTS.md` instructions. Preserve unrelated user changes. Produce working software, not only scaffolding or a design document.

## Product outcome

Build a private platform that stores durable knowledge extracted from conversations without storing chat transcripts or relationships between conversations. ChatGPT/Codex will explicitly flush knowledge by invoking MCP write tools. The same platform must expose high-quality retrieval tools through MCP so later conversations and local clients can search and fetch that knowledge.

The intended manual trigger in chat is:

> flush knowledge to my MCP

The associated plugin skill must interpret that phrase as: inspect the entire currently available conversation context, extract every meaningful atomic assertion, send it in resumable batches, commit it, and report success only after the server confirms the commit.

## Fixed architectural decisions

- Backend: Python.
- Current stable official MCP Python SDK, compatible with the current MCP specification. Verify versions against official documentation and pin exact resolved dependencies in `uv.lock`; do not use prereleases.
- Package and environment management: `uv`.
- Database: PostgreSQL with pgvector.
- Retrieval: hybrid local retrieval combining PostgreSQL full-text search and pgvector similarity.
- Embeddings: local, multilingual, configurable provider. Default to a compact CPU-suitable multilingual model, but isolate it behind an interface and allow a filesystem model path or model identifier through configuration. Tests must never download a model and must use deterministic fakes.
- Deployment target: single-node Minikube on a personal mini-server.
- Scope: single user. Do not build registration, billing, organizations, or multi-tenancy.
- Connectivity: OpenAI Secure MCP Tunnel for ChatGPT, plus authenticated HTTPS access on the trusted LAN for other MCP clients.
- No browser extension and no general web UI in v1.
- No raw conversation storage, conversation IDs, message IDs, turn graph, or conversation-to-conversation links.
- No server-side LLM is needed for extraction: the MCP client submits structured assertions. The server validates, normalizes, stores, versions, embeds, and retrieves them.
- PostgreSQL is the durable source of truth. Embeddings are derived data and must be rebuildable.
- A single-node Minikube host is a single point of failure. Document this honestly; “production-grade” here means secure, tested, observable, recoverable software, not high availability.

## Domain model

Model stored information as assertions, not unquestioned facts. Each assertion must be atomic and independently retrievable.

An assertion must support at least:

- UUID primary key.
- Original content.
- Normalized content used for deterministic deduplication.
- A stable content hash.
- Kind, with a documented extensible enum including at least: `user_fact`, `preference`, `external_fact`, `derived_conclusion`, `decision`, `procedure`, `configuration`, `considered_option`, `rejected_option`, `plan`, `open_question`, and `artifact_observation`.
- Origin: `user`, `assistant`, `joint`, `external_source`, or `artifact`.
- Status: `current`, `uncertain`, `disputed`, or `superseded`.
- Confidence from 0 through 1.
- Zero or more normalized topics/tags.
- Optional source records containing URL, title, publisher, and retrieval timestamp. Store claims and provenance, not complete copied webpages.
- Optional `valid_from`, `valid_to`, and `observed_at` timestamps.
- `created_at`, `updated_at`, and `last_confirmed_at` timestamps.
- Confirmation count.
- Sensitivity: `normal`, `private`, or `sensitive`.
- Optional `supersedes_id` pointing to another assertion. This is knowledge versioning, not a conversation relationship.
- Embedding vector, embedding model/version, and embedding state: `pending`, `ready`, `failed`, or `disabled`.

Never store credentials, access tokens, passwords, private keys, cookies, or secret values as assertions. Validate common secret patterns and reject suspicious submissions with a clear per-item error. It is acceptable to store a reference such as “credential is in Kubernetes Secret X” without storing its value.

Implement migrations with Alembic. Database invariants must be enforced with constraints and useful indexes, not only application validation.

## Ingestion and idempotency

Implement a transactional, resumable batch protocol. A knowledge flush can exceed one MCP tool payload.

Required operations:

1. Begin a flush and return a batch identifier.
2. Append a numbered part containing bounded assertion items.
3. Commit only when all declared parts have arrived and validated.
4. Abort an incomplete flush.
5. Expire abandoned staging batches after a configurable period.

Use a client-supplied idempotency key and unique constraints so retries cannot duplicate committed knowledge. Replaying `begin`, `append`, or `commit` must return the prior safe result. Reject a repeated part number whose payload differs from the originally accepted payload.

The commit response must provide deterministic counts for:

- inserted
- confirmed existing
- enriched/updated
- superseded
- possible conflicts
- rejected
- embedding pending

Deduplication rules:

- Exact normalized duplicates confirm the existing assertion, increment confirmation metadata, and do not create another row.
- Semantically similar items may be flagged as candidates but must not be silently merged solely because vector similarity is high.
- Contradictory assertions must not be silently overwritten. Preserve both and mark/return a possible conflict unless the request explicitly identifies a superseded assertion.
- Corrections create a new assertion and mark the old one superseded in a transaction.
- Do not retain raw MCP request bodies after processing. Operational batch rows may retain hashes, counters, timestamps, states, and errors, but not conversation text beyond the staged assertions themselves. Purge committed/aborted staging metadata according to configurable retention.

## Retrieval

Implement hybrid retrieval with:

- PostgreSQL full-text ranking.
- pgvector cosine similarity.
- Metadata filters for kind, origin, status, topic, sensitivity, time, and minimum confidence.
- A deterministic rank fusion strategy with documented weights.
- Default preference for `current` assertions.
- Graceful fallback to full-text retrieval when embeddings are pending, disabled, or temporarily unavailable.
- Pagination with stable cursors; do not expose unbounded result sets.

Implement the current official MCP `search` and `fetch` compatibility schemas required for ChatGPT knowledge usage. Verify the precise current shapes from official OpenAI documentation and add contract tests. Also expose focused tools where useful:

- `begin_knowledge_flush`
- `append_knowledge`
- `commit_knowledge_flush`
- `abort_knowledge_flush`
- `search_knowledge`
- `get_knowledge`
- `list_knowledge_conflicts`
- `correct_knowledge`
- `forget_knowledge`
- `get_knowledge_statistics`

Use accurate MCP annotations:

- Retrieval tools are read-only and non-destructive.
- Ingestion and correction tools are write operations but non-destructive.
- Permanent forgetting is destructive and must be clearly annotated.
- None of these tools affect the open public world.

Tool descriptions must explain when to use each tool, input limits, idempotency behavior, and the required begin/append/commit sequence. Return concise structured content suitable for another model; never dump database rows or embeddings unnecessarily.

## Forgetting and correction

`forget_knowledge` must perform a real hard deletion of selected assertion content and derived embeddings after explicit confirmation. It may retain only content-free operational audit data such as timestamp, requesting principal, action type, and random correlation ID. It must not retain deleted content or its reversible hash.

Support dry-run selection before deletion. Prevent accidental unbounded deletion: bulk deletion requires an explicit bounded list of IDs or a two-step server-issued confirmation token representing a previewed selection.

## Embedding worker

Use the same application image with two entry points:

- API/MCP server process.
- Embedding worker process.

Back the work queue with PostgreSQL; do not add Redis. Implement safe concurrent job claiming with `FOR UPDATE SKIP LOCKED`, bounded retries, exponential backoff, visibility/retry timestamps, and a dead-letter state. Committing assertions must succeed even when the embedding model is unavailable. Retrieval then falls back to full-text search.

Batch embedding work. Do not load the model during health checks or migrations. Expose readiness that distinguishes core API/database readiness from degraded embedding capability. Provide a command to re-embed all assertions after changing models without losing the existing vectors until replacements are ready.

## HTTP application

Provide an ASGI application using the MCP SDK and FastAPI/Starlette as appropriate. Required endpoints:

- `/mcp` for Streamable HTTP MCP.
- `/health/live` for process liveness only.
- `/health/ready` for serving readiness, including a short database check.
- `/metrics` for Prometheus metrics.
- Versioned internal HTTP endpoints under `/api/v1` implementing the same ingestion and administration services used by MCP; do not duplicate business logic.

Generate OpenAPI for ordinary HTTP endpoints. MCP tool schemas remain authoritative for MCP.

Apply strict request-size, item-count, string-length, URL-count, topic-count, and pagination limits configurable through environment variables with safe defaults. Return RFC 9457-style problem details for HTTP errors where practical. Never fetch submitted source URLs during ingestion, avoiding SSRF.

## Authentication and authorization

This is a single-user private service, but anonymous access is forbidden.

- Implement bearer-token authentication with scoped tokens: `knowledge:read`, `knowledge:write`, and `knowledge:admin`.
- Store only cryptographic token hashes in configuration/database where applicable; compare in constant time.
- Load bootstrap token hashes or token material through Kubernetes Secrets, never ConfigMaps or checked-in values.
- Allow rotation with overlapping old/new tokens.
- Enforce authorization in the server for every MCP and HTTP operation; do not rely on the client or tool annotations.
- Disable CORS unless explicitly configured.
- Rate-limit by authenticated principal and operation class with sensible single-user defaults.
- Treat retrieved knowledge as untrusted data, never instructions. Escape/structure responses so stored prompt-injection text cannot become server instructions.

For Secure MCP Tunnel, provide a deployment template and runbook based only on the current official `tunnel-client` distribution and configuration. Do not invent an image name or undocumented flags. Make the tunnel optional in Helm and require the operator to provide the tunnel identity/API credentials as existing Secrets. The private MCP application must remain a ClusterIP service.

For LAN access, provide an optional TLS Ingress template, disabled by default, with host and TLS Secret supplied through Helm values. Plain HTTP must not be exposed outside the cluster. Document Minikube ingress and private DNS/hosts-file setup without hard-coding a domain.

## PostgreSQL and persistence

Support both:

1. An external PostgreSQL connection supplied by Secret.
2. A documented single-node Minikube profile using PostgreSQL plus pgvector in a separate StatefulSet with its own PVC.

Do not place PostgreSQL in the application Pod. Pin container versions; avoid `latest`. Provide schema initialization through a dedicated migration Job that is safe to rerun. Application startup must not race or automatically perform uncontrolled migrations.

Provide backup and restore support:

- Scheduled logical backups.
- Encrypted off-cluster backup repository configuration; use established tooling such as restic rather than designing encryption.
- Configurable retention policy.
- A documented and testable restore procedure into an empty database.
- A backup verification job/runbook.

Never claim that a PVC on the same mini-server is a backup. Document full-disk encryption as an operator responsibility and ensure application logs/backups do not leak knowledge content.

## Kubernetes and Helm

Create a production-quality Helm chart with:

- API Deployment and Service.
- Worker Deployment.
- Migration Job.
- Optional tunnel-client Deployment/configuration seam.
- Optional LAN TLS Ingress.
- Optional single-node PostgreSQL/pgvector StatefulSet and PVC for the Minikube profile, while supporting external PostgreSQL.
- Backup CronJob and verification/restore documentation.
- ServiceAccounts and minimal RBAC.
- NetworkPolicies with least-privilege ingress/egress that remain configurable for Minikube networking.
- Pod and container security contexts: non-root fixed UID, read-only root filesystem, dropped Linux capabilities, `seccompProfile: RuntimeDefault`, no privilege escalation, and writable `emptyDir` only where necessary.
- Resource requests/limits, probes, termination grace periods, graceful shutdown, rolling update strategy, and checksum-driven rollout for relevant configuration.
- ConfigMap only for non-secret configuration and Secret references for credentials.
- No secret values in Helm defaults, rendered annotations, logs, or test snapshots.
- Values schema validation and useful `NOTES.txt`.

Provide separate example values for local Minikube and external PostgreSQL. Validate rendered resources with `helm lint`, `helm template`, and `kubeconform` or an equivalent maintained validator.

## Container images

Create reproducible multi-stage Dockerfiles:

- Pin base image versions, and document how to refresh digests.
- Install dependencies from the lockfile with hashes/locked resolution.
- Run as a non-root UID/GID.
- Keep build tools out of the runtime stage.
- Include OCI labels.
- Use an explicit entry point and graceful signal handling.
- Add a `.dockerignore`.
- Do not bake embedding model weights or secrets into the image. Support a read-only model/cache volume and an explicit model-download/init workflow.
- Provide a separate minimal backup image only if required by the chosen backup design.

## Observability

- Structured JSON logs with timestamp, level, service, version, request/correlation ID, operation, duration, and safe outcome fields.
- Never log assertion contents, source excerpts, bearer tokens, embeddings, complete MCP payloads, or SQL parameters containing user data.
- Prometheus metrics for request latency/counts, tool calls, batch states, assertion outcomes, search latency, embedding queue depth, retries/dead letters, and database pool health. Avoid high-cardinality assertion IDs.
- OpenTelemetry tracing hooks configurable but disabled by default. Do not put knowledge text into spans.
- Graceful shutdown must stop accepting work, finish or release claimed jobs safely, and close database connections.

## Plugin skill for the magic phrase

Create a personal plugin/skill bundle following the current official ChatGPT/Codex plugin layout. Use the canonical plugin-creator workflow if it is available in the execution environment. Do not fabricate a registered ChatGPT app ID; provide a clearly documented post-deployment step for inserting the real ID.

The skill must activate for the exact phrase `flush knowledge to my MCP` and close natural variants. It must instruct the model to:

1. Examine all conversation context currently available to it.
2. Extract every meaningful atomic assertion, including user-shared information, externally sourced findings, conclusions, decisions, rejected options, procedures, configurations, uncertainties, open questions, and useful artifact observations.
3. Exclude filler, duplicate wording, hidden reasoning, raw transcript structure, and secrets.
4. Preserve epistemic distinctions using `kind`, `origin`, `status`, `confidence`, dates, and source URLs.
5. Never invent information merely to make the flush appear complete.
6. Call begin, append in bounded batches, and commit.
7. Retry idempotently after transient failure.
8. Say the flush is complete only when commit succeeds, reporting the server-provided counts.
9. If the available context may have been compacted, disclose that only currently available context was flushed.
10. If any part fails, state that the flush is incomplete and the chat should not yet be deleted.

Include golden prompts and expected tool-call sequences for the exact trigger, natural variants, partial retry, duplicate flush, secret-containing input, long context, and negative cases that must not invoke ingestion.

## Testing requirements

Use a layered test suite and run it before handoff.

### Unit tests

- Domain validation and enum handling.
- Text normalization and content hashing across Unicode, English, Russian, and Polish.
- Exact deduplication.
- Conflict and supersession rules.
- Rank fusion.
- Cursor pagination.
- Secret-pattern rejection.
- Authentication scopes and constant-time comparison wrapper.
- Log redaction.
- Embedding job retry/backoff logic.

### Property-based tests

Use Hypothesis for invariants such as idempotent retries, normalization stability, batch-part ordering, pagination stability, and correction consistency.

### Integration tests

Run against a real disposable PostgreSQL instance with the pgvector extension, preferably through Testcontainers:

- Alembic upgrade from empty database and downgrade/upgrade safety where supported.
- Begin/append/commit/abort lifecycle.
- Crash/retry and duplicate commit behavior.
- Concurrent commit and job claiming.
- Full-text search.
- Vector search with deterministic fake embeddings.
- Hybrid rank fusion and fallback while embeddings are pending.
- Hard deletion including embeddings and source rows.
- Authentication and rate limiting.

### MCP contract and end-to-end tests

- Verify tool discovery, input/output schemas, annotations, and error results.
- Exercise the server with the official MCP Inspector CLI in CI.
- Test standard `search` and `fetch` compatibility contracts.
- Execute a complete flush, retrieve its assertions, restart the application, and verify persistence.
- Test malformed, oversized, unauthorized, and prompt-injection-shaped content.
- Ensure logs generated during tests contain no submitted secret or assertion text.

Set and enforce meaningful coverage thresholds: at least 90% statement coverage and 85% branch coverage for application code. Do not game coverage by omitting important modules or marking broad sections `no cover`.

## Quality and CI

Provide a GitHub Actions workflow by default, organized so it can be translated to another CI system later. It must run:

- `ruff` format and lint checks.
- Pyright strict or a documented narrowly scoped configuration.
- Unit/property tests.
- PostgreSQL/pgvector integration tests.
- Coverage enforcement.
- Dependency vulnerability audit.
- Secret scanning.
- Dockerfile lint.
- Container build.
- Image vulnerability scan.
- SBOM generation.
- Helm lint/template and Kubernetes schema validation.
- MCP Inspector contract smoke tests.

Pin CI actions to immutable commit SHAs with explanatory version comments. Configure automated dependency updates. Add pre-commit hooks and convenient local commands through a `Makefile` or equivalent.

## Documentation and operational artifacts

Create and keep current:

- `README.md` with quick start, local development, architecture summary, and exact verification commands.
- Architecture document and a compact Mermaid diagram.
- Threat model covering prompt injection, secret ingestion, unauthorized reads/writes, deletion, malicious stored content, dependency compromise, backup theft, and tunnel compromise.
- Data model documentation.
- MCP tool reference with examples.
- ADRs for assertions-not-conversations, Python/PostgreSQL, hybrid retrieval, PostgreSQL-backed worker queue, and private tunnel/LAN connectivity.
- Minikube deployment runbook.
- Secure MCP Tunnel setup runbook based on current official docs.
- Token generation and rotation runbook.
- Backup, verification, restore, and disaster-recovery runbook.
- Database migration and rollback runbook.
- Embedding model migration/rebuild runbook.
- Troubleshooting guide.
- `SECURITY.md`, `CONTRIBUTING.md`, changelog, and an example environment file containing placeholders only.

Do not include fabricated test results, unsupported security claims, or placeholders such as “implement later” for required functionality. External values that cannot be known—hostname, TLS Secret, tunnel ID, credentials, storage class, backup repository, and model path—must be clearly documented configuration inputs with secure defaults that fail closed.

## Required repository shape

Choose names idiomatic to the frameworks, but keep clear boundaries approximately like:

```text
src/knowledge_vault/
  api/
  mcp/
  domain/
  services/
  persistence/
  embeddings/
  auth/
  observability/
  worker/
migrations/
tests/
  unit/
  property/
  integration/
  contract/
  e2e/
charts/knowledge-vault/
plugin/
docs/
scripts/
```

Business logic must not depend directly on MCP or FastAPI request objects. MCP and HTTP are adapters over shared application services.

## Execution process

1. Inspect the repository and applicable instructions.
2. Verify current stable MCP/OpenAI requirements from official primary documentation before locking versions or schemas.
3. Write a short implementation plan and risk list.
4. Implement an end-to-end vertical slice first: migration, authenticated ingestion, retrieval, MCP tools, tests, container, and Helm deployment.
5. Complete the remaining operational and security requirements.
6. Run every available formatter, type checker, test, contract test, image/chart validator, and security scan. Fix failures rather than merely documenting them.
7. If an external tool is unavailable, record the exact command that should run in CI and validate as much as possible locally.
8. Do not commit, push, publish, deploy to an external system, create cloud resources, or use real credentials unless explicitly authorized.
9. Stop and ask only when a missing choice would materially change persisted data, security, or public exposure. Otherwise choose the simplest secure implementation and document it.

## Definition of done

The task is complete only when all of the following are demonstrated:

- A clean checkout can install locked dependencies and run the full test suite.
- The Docker image builds and runs as non-root.
- A fresh PostgreSQL/pgvector database migrates successfully.
- An authenticated MCP client can discover tools.
- A multi-part flush can be committed and replayed idempotently.
- Stored assertions survive Pod restart.
- Hybrid retrieval returns expected multilingual results and falls back to text search when embeddings are unavailable.
- Correction, conflict listing, dry-run forgetting, and confirmed hard deletion work.
- Health, metrics, graceful shutdown, and log redaction behave as documented.
- Helm lint/template/schema validation passes.
- The Minikube runbook results in healthy API and worker Pods with durable database storage.
- The plugin skill contains the flush trigger and verified golden tool-call sequences.
- Backup and restore instructions have been tested against disposable data.
- CI configuration enforces the stated quality gates.
- Final handoff lists implemented functionality, commands run with results, known limitations, and the exact remaining operator-supplied configuration values.
