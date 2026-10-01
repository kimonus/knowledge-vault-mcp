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

### Added

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
