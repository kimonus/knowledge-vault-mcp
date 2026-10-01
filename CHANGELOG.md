# Changelog

All notable changes follow Keep a Changelog. This project uses semantic versioning after its first
stable release.

## [Unreleased]

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
