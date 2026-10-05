# Changelog

All notable changes follow Keep a Changelog. This project uses semantic versioning after its first
stable release.

## [Unreleased]

### Changed

- **Flushes are incremental.** The delivered instructions, the tool descriptions and the flush
  skill now tell a client to search the vault once per subject before `begin_knowledge_flush` and
  to submit only what is new, changed or refined. The server merges only identical normalized
  text, so a repeated flush of a re-worded conversation used to insert every assertion again.
  A client may now supersede an assertion that it read in the session and that the conversation
  explicitly changes; before, a supersession needed an ID already known to the conversation.
- Clients no longer read every committed record back. They read only items whose outcome was not
  the planned one, that report ignored fields, or that created a possible conflict.
- **Operator action required.** Roll the API out and reconnect clients so that they receive the
  new instructions; update a locally installed flush skill and any copied fallback agent rules
  from `docs/runbooks/client-setup.md`.

### Added

- `commit_knowledge_flush`, `correct_knowledge` and the matching HTTP routes return `items`: for
  each accepted item its index, assertion ID, outcome, the assertion it superseded, the possible
  conflicts it created, and the submitted fields that an exact-content match did not apply. The
  entries hold no assertion text. No migration is needed.

### Fixed

- Both images upgrade `libpcre2-8-0` to `10.42-1+deb12u2`, which fixes CVE-2026-103111 (an
  out-of-bounds write on a crafted regular expression). No published digest of the pinned Python
  or PostgreSQL base image contains the fix yet, so the Dockerfiles install the pinned package;
  the step is to be removed when the base digests are next moved. Images released as 0.2.0 and
  earlier carry the vulnerable package.
- Two `begin` calls that arrived together with the same idempotency key could both try to create
  the batch (or artifact upload), and one failed with an internal error instead of returning the
  other's record. The loser now returns the existing record. The test for this had been passing
  by timing and now forces the race.

## [0.2.0] - 2026-10-03

### Added

- **Text artifacts.** A file that is itself part of the knowledge—CSV, code, Markdown, JSON—can
  be stored whole and linked to the assertions that describe it: `begin_knowledge_artifact`,
  `append_knowledge_artifact`, `commit_knowledge_artifact`, `get_knowledge_artifact`, the
  `artifact_ids` field on assertions, and the matching `/api/v1/artifacts` routes. Uploads are
  chunked and idempotent, identical content is stored once, a secret-shaped value discards the
  whole artifact, and an artifact is deleted when no assertion references it, including through
  `forget_knowledge`. Binary files are refused. Assertion responses gain an `artifacts` list,
  the forget preview and result gain `artifact_count` and `deleted_artifact_count`, and
  statistics gain `artifacts_total`.
- **Operator action required.** The upgrade adds revision `0004_text_artifacts`; the chart's
  migration Job applies it. Artifact text is stored in PostgreSQL and is covered by the existing
  backup. Reconnect clients so that they receive the new tools and guidance.

## [0.1.1] - 2026-10-03

### Fixed

- **Upgrade recommended for every deployment with embeddings enabled.** Searches that arrived
  together before the embedding model was loaded each loaded their own copy—more than a gigabyte
  apiece—and the API was killed for memory; clients saw the tool fail. The model is now loaded
  once per process however many requests are waiting, and encoding runs one call at a time.
- The release workflow published a `latest` image tag with 0.1.0 although the release policy
  says it publishes none: the metadata action adds it to semver releases by default. It is now
  disabled, and a test keeps it disabled. The `latest` tag on the 0.1.0 images stays where it is
  and must not be used; pin the digest.

## [0.1.0] - 2026-10-02

### Added (operational consistency)

- Heartbeats for the worker and the backup job, reported by `get_knowledge_statistics` and as a
  metric, and an optional watchdog CronJob that fails—and can notify a webhook—when the worker
  stops, backups stop, or embedding jobs die (revision `0003_operational_heartbeats`).
- Chart values for an existing PostgreSQL claim, backups to a host directory under a chosen user,
  `extraEnv`, and `extraObjects`, with a complete Traefik and host-path example, so that a
  deployment needs no hand-written manifests beside the chart.
- A manual rehearsal mode for the release workflow that publishes nothing.

### Fixed (operational consistency)

- The chart rendered integer settings of one million or more in exponent form, so the default
  `config.maxRequestBytes` reached the service as `2e+06` and the API and worker refused to
  start. Integer settings are now rendered as integers, and CI rejects exponent-form values in
  every rendered manifest.

- The chart's default API memory limit (1 GiB) was below what the embedding model needs once the
  API loads it for the first search (about 1.3 GiB). The default is now 2 GiB with a 512 MiB
  request, and the embeddings runbook states the measured requirement.

### Changed (operational consistency)

- The HTTP API and the MCP tools return the same response models. HTTP responses for assertions
  and searches now carry `untrusted_data`, append and abort responses include the batch ID, and
  the OpenAPI document describes responses.
- The service version is read from the installed package.

### Changed

- MCP initialization now delivers exhaustive extraction guidance with fixed trigger, safety,
  idempotency and readback rules. The client workflow verifies every distinct committed assertion
  ID and reports rejections, discrepancies and unavailable context without promising lossless chat
  preservation. Tool schemas and persistence contracts are unchanged.
- Added `config.ingestionPolicyVersion` and optional `config.ingestionPolicy` Helm values, mapped
  to `KNOWLEDGE_VAULT_INGESTION_POLICY_VERSION` and `KNOWLEDGE_VAULT_INGESTION_POLICY`. The packaged
  policy is used when no override is supplied; overrides are bounded and reject secret shapes.
- **Operator action required.** Deploy the updated application image, upgrade using the complete
  operator values file, reconnect clients and refresh the ChatGPT plugin connection. ConfigMap
  edits alone do not refresh running Pods or existing client sessions. See the Minikube policy
  configuration runbook.

Remediation of the [2026-10-01 independent review](docs/reviews/2026-10-01-independent-solution-review.md);
finding IDs in parentheses.

- **Operator action required.** With Cloudflare Access enabled the chart now requires
  `cloudflareAccess.publicHosts`, and the service refuses to start unless a real published
  hostname is configured. `cloudflareAccess.privateHosts` optionally restricts device bearer
  tokens to the listed private hostnames (KV-003).
- **Operator action required.** `ingress.className` no longer defaults to `nginx` and must be set
  when the ingress is enabled; unknown keys in chart values are rejected by the schema (KV-020).
- **Operator action required.** Embedding weights are loaded from local files only. Mount a
  populated cache through `modelCache.existingClaim`, or set
  `KNOWLEDGE_VAULT_EMBEDDING_ALLOW_DOWNLOAD=true` on a development machine (KV-011).
- `append_knowledge` rejects invalid or secret-shaped items individually over MCP instead of
  failing the whole part, and advertises its item bound (KV-004).
- MCP tool errors carry a stable `code: message`; HTTP returns 4xx problem details for malformed
  requests and 413 for oversized streamed bodies (KV-005).
- Corrections whose wording already exists supersede the named assertion and revive the existing
  one; `superseded` counts reflect what changed; re-affirming an uncertain or disputed assertion
  as current updates its status; superseding an assertion resolves its open conflicts (KV-008,
  KV-024).
- Retrieval returns provenance `sources`; `get_knowledge` and `search_knowledge` label results as
  untrusted data (KV-012).
- Timestamps without a UTC offset are interpreted as UTC.
- A worker embeds only jobs for its configured model and search ignores vectors produced by
  another model; `knowledge-vault-reembed` can be rerun and requeues dead jobs (KV-010).
- NetworkPolicies are rendered per component, so the bundled PostgreSQL is reachable from the
  application Pods on clusters that enforce them, and from nothing else (KV-014).
- API and worker init containers wait for the newest revision in their image rather than a
  hard-coded revision name (KV-013).
- **Operator action required.** The chart's default images are now the published
  `ghcr.io/kimonus/knowledge-vault-mcp` images (`values-minikube.yaml` selects the local build),
  and the chart ingress routes only `/mcp`, `/api/v1`, and `/.well-known` (KV-018, KV-020).
- OAuth protected-resource metadata is advertised only for published hostnames; the private
  endpoint answers with a plain `Bearer` challenge (KV-021).
- Possible conflicts are detected whether or not the assertions share a topic (KV-024).
- `generate_token.py` no longer accepts the pepper as a command-line argument (KV-024).
- The release workflow scans the pushed image digests and no longer publishes a `latest` tag
  (KV-023).
- Request metrics use a fixed set of route labels; uvicorn access logging is disabled in favour of
  the structured request log (KV-018).

### Added

- Database CHECK constraints for every enumerated column (revision
  `0002_enumerated_value_checks`); revision `0001_initial` now spells out its DDL (KV-013).
- Expiry of abandoned flush batches, purging of staging metadata and confirmation tokens, and
  expiry enforcement at commit (KV-009).
- Embedding claim leases, per-item fallback when a batch cannot be embedded, a worker metrics
  port with a liveness probe, and model-load back-off with a `degraded` readiness state (KV-010,
  KV-011, KV-018).
- Rate limits on MCP tools, `Retry-After` on HTTP 429, and throttled signing-key refresh (KV-015).
- `Origin` validation on the MCP endpoint (KV-019).
- Tool-call, search-latency, and connection-pool metrics (KV-018).
- Optional, content-free request spans behind `KNOWLEDGE_VAULT_OTEL_ENABLED` (KV-018).
- A test that the pgvector image pin and the project version agree in every file (KV-023).
- Regression tests through the real HTTP and MCP transport stack, concurrency tests, and a backup
  smoke test that runs read-only and asserts retention.

### Fixed

- The backup job failed under its read-only root filesystem because restic had no writable cache
  directory (KV-001).
- Backup retention never pruned because every snapshot formed its own group (KV-002).
- Assertion text and statement parameters could reach logs through database error messages and
  unstructured tracebacks (KV-006).
- Secret detection missed connection strings, cookies, several common token formats, this
  service's own device tokens, Unicode-obfuscated values, and every field other than `content`
  (KV-007).
- Host-header variants (trailing dot, repeated or missing header) let a device bearer token skip
  the assertion requirement for the published hostname at the origin (KV-003).
- Jobs claimed by a killed worker were never retried (KV-010).
- Documented token bootstrap commands and the MCP tool example did not work as written; a single
  generated token record is now accepted as the bootstrap value (KV-016).
- Setting `nodeSelector`, `tolerations`, or `affinity` produced invalid manifests (KV-020).
- A repeated source URL within one assertion made the commit fail; commit no longer retries
  constraint violations that cannot succeed.
- Concurrent first commits of identical new content failed with a database error instead of
  confirming the same assertion (KV-024).

### Removed

- Unused runtime dependencies `orjson` and `python-json-logger`.

### Added (initial implementation)

- Initial MCP and HTTP service with authenticated resumable assertion ingestion.
- PostgreSQL/pgvector persistence, full-text/vector hybrid retrieval, and embedding worker.
- Correction, conflict review, statistics, and preview/confirmation hard deletion.
- Structured redacted observability, health checks, rate/size limits, containers, Helm chart,
  backup job, operational documentation, tests, and Codex plugin skill.
- Digest-required Cloudflared Helm deployment for the hosted-client endpoint and an end-to-end
  backup and restore smoke test in CI.
- Community health files, structured issue and pull-request templates, support and release
  policies, and a signed tag-driven GHCR release workflow with SBOM and provenance attestations.
- Documentation clarifying that Cloudflare + LAN/WireGuard is the maintained homelab reference
  profile rather than a universal MCP connectivity requirement.

### Security

- Pepper-HMAC bearer token storage, per-tool scopes, secret-shaped input rejection, non-root
  read-only containers, fail-closed chart configuration, and private connectivity modes.
- Raised the minimum PyJWT version and refreshed locked transitive dependencies and container base
  images in response to fixed upstream vulnerabilities.
- Patched restic's Go dependency graph and fixed backup snapshots to restore to a stable path.
