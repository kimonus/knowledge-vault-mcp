# Knowledge Vault

[![CI](https://github.com/kimonus/knowledge-vault-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/kimonus/knowledge-vault-mcp/actions/workflows/ci.yml)

Knowledge Vault is a private, single-user MCP and HTTP service for durable facts extracted from
conversations. It stores atomic assertions and provenance, never chat transcripts. PostgreSQL
provides durable state and full-text search; pgvector and a local multilingual model add semantic
retrieval without sending assertion text to a hosted embedding service.

The implementation includes resumable/idempotent ingestion, explicit correction and conflict
records, two-step hard deletion, scoped bearer authentication, rate and size limits, structured
redacted logs, Prometheus metrics, a PostgreSQL-backed embedding worker, containers, a Helm chart,
and a Codex plugin skill activated only by an explicit request such as
`flush knowledge to my MCP`.

The server also sends extraction guidance through MCP initialization, so supporting remote clients
receive the workflow without installing a local skill. It preserves exact substantive details and
requires readback of committed records. Operators can customize the non-secret guidance through
the chart's ConfigMap-backed `config.ingestionPolicy` and `config.ingestionPolicyVersion` values;
see [policy configuration](docs/runbooks/minikube.md#configure-client-ingestion-guidance). Policy
changes require a rollout and client reconnection/refresh. The server sees submitted assertions,
so it cannot guarantee lossless preservation of unseen conversation history.

MCP connectivity is a deployment concern, not a product requirement. This repository documents
Cloudflare Tunnel + Access and a LAN/WireGuard endpoint because they fit the maintainer's reference
homelab. Other environments may use a different authenticated HTTPS gateway, private overlay,
identity provider, or managed MCP transport. Any alternative must preserve origin-side
authentication, scoped authorization, TLS, private bearer-token boundaries, and fail-closed
configuration.

## Quick start

Prerequisites: Python 3.12+, [uv](https://docs.astral.sh/uv/), Docker, and a Docker daemon.

```bash
uv sync --locked
cp .env.example .env
docker run --name knowledge-vault-postgres --rm -d \
  -e POSTGRES_USER=knowledge_vault \
  -e POSTGRES_PASSWORD=local-only-password \
  -e POSTGRES_DB=knowledge_vault \
  -p 5432:5432 \
  pgvector/pgvector:0.8.1-pg17-trixie@sha256:137f044b0efe3d57f39b972b9b53641b1f2045b99d879e298bbf514a25787dcf
uv run alembic upgrade head
printf '%s' "$KNOWLEDGE_VAULT_TOKEN_PEPPER" | \
  uv run python scripts/generate_token.py --pepper-stdin
```

Put the printed `RECORD` in `KNOWLEDGE_VAULT_BOOTSTRAP_TOKENS` (one record object or a JSON array
of records), retain the one-time `TOKEN` in a password manager, and use the same pepper in
`KNOWLEDGE_VAULT_TOKEN_PEPPER`. Model weights are loaded from local files only: either set
`KNOWLEDGE_VAULT_EMBEDDINGS_ENABLED=false` for full-text operation, or install the `embeddings`
extra and set `KNOWLEDGE_VAULT_EMBEDDING_ALLOW_DOWNLOAD=true` once on a development machine to
fetch the model. Then run the API and worker in separate terminals:

```bash
uv run knowledge-vault-api
uv run knowledge-vault-worker
```

The MCP endpoint is `http://127.0.0.1:8000/mcp`; the HTTP API is under `/api/v1`. Health endpoints
are `/health/live` and `/health/ready`. Do not expose this development HTTP endpoint beyond the
loopback interface. Production access must use authenticated TLS. The reference deployment uses
Cloudflare Access OAuth for hosted clients and a private LAN/WireGuard endpoint with a scoped
per-device token for direct clients; this is not the only valid connectivity architecture.

## Verification

```bash
uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -q
uv run pytest --cov --cov-branch --cov-report=term-missing --cov-report=xml
docker build --pull=false -t knowledge-vault:local .
helm lint charts/knowledge-vault -f charts/knowledge-vault/values-minikube.yaml
helm template knowledge-vault charts/knowledge-vault \
  -f charts/knowledge-vault/values-minikube.yaml > rendered.yaml
kubeconform -strict -summary -kubernetes-version 1.33.0 rendered.yaml
docker build --pull=false -f Dockerfile.backup -t knowledge-vault-backup:local .
sh scripts/ci/test_backup_restore.sh knowledge-vault-backup:local
# If the Codex plugin validator is installed:
python3 "$CODEX_HOME/skills/.system/plugin-creator/scripts/validate_plugin.py" \
  plugin/knowledge-vault
```

Integration and end-to-end tests use Testcontainers and require Docker. The coverage configuration
enforces 90% statement coverage; the CI job separately checks 85% branch coverage.

## Configuration

All runtime variables use the `KNOWLEDGE_VAULT_` prefix. Start from [.env.example](.env.example),
which contains placeholders only. Production fails closed without a token pepper. The Helm chart
also refuses to render without existing auth/database Secrets and explicit public/issuer URLs.

The bearer token configuration is a JSON list of records (a single record object is also
accepted) with `principal_id`, an HMAC-SHA256 digest in `sha256`, and scopes selected from
`knowledge:read`, `knowledge:write`, and `knowledge:admin`. Plaintext bearer tokens are never
stored by the service.

When Cloudflare Access is enabled, the published hostname must be configured, and the private
hostname should be:

| Helm value | Environment variable | Meaning |
|---|---|---|
| `cloudflareAccess.publicHosts` | `KNOWLEDGE_VAULT_CLOUDFLARE_ACCESS_PUBLIC_HOSTS` | Required. Hostname(s) served through the tunnel. Requests for them are refused without a valid signed Access assertion, whatever other credential they carry |
| `cloudflareAccess.privateHosts` | `KNOWLEDGE_VAULT_CLOUDFLARE_ACCESS_PRIVATE_HOSTS` | Recommended. Hostname(s) of the LAN/WireGuard endpoint. When set, device bearer tokens are accepted only for these hostnames |

Both are lists of bare hostnames, without scheme, port, or path. Set them in the operator-local
values file used for the release, next to the other `cloudflareAccess` settings:

```yaml
cloudflareAccess:
  enabled: true
  existingSecret: knowledge-vault-cloudflare-access
  publicHosts: [PUBLIC_MCP_HOST]
  privateHosts: [PRIVATE_MCP_HOST]
```

Tracing is off by default. `KNOWLEDGE_VAULT_OTEL_ENABLED=true` emits one span per HTTP request
(method, fixed route label, status, exception type—never content) alongside the MCP SDK's
message spans, and exports them with the standard `OTEL_EXPORTER_OTLP_*` variables. It requires
an image that additionally installs `opentelemetry-sdk` and
`opentelemetry-exporter-otlp-proto-http`; they are not in the locked dependency set because the
SDK depends on a pre-release package, and the service refuses to start if the flag is set
without them.

Other settings added for operations: `KNOWLEDGE_VAULT_EMBEDDING_ALLOW_DOWNLOAD` (development
only), `KNOWLEDGE_VAULT_EMBEDDING_CLAIM_TIMEOUT_SECONDS`,
`KNOWLEDGE_VAULT_MAINTENANCE_INTERVAL_SECONDS`, and `KNOWLEDGE_VAULT_WORKER_METRICS_PORT` (Helm
`worker.metricsPort`). MCP tools and HTTP routes share the per-principal
`KNOWLEDGE_VAULT_RATE_*_PER_MINUTE` limits.

Embedding model weights are not baked into the application image and are never downloaded by a
running Pod. Pre-populate a persistent model cache in a controlled step and mount it through
`modelCache.existingClaim` (see [embeddings.md](docs/runbooks/embeddings.md)).
Set `KNOWLEDGE_VAULT_EMBEDDINGS_ENABLED=false` for deterministic full-text-only operation.

## Architecture and operations

- [Architecture](docs/architecture.md) and [data model](docs/data-model.md)
- [Independent solution review prompt](independent-solution-review-prompt.md), the
  [2026-10-01 review](docs/reviews/2026-10-01-independent-solution-review.md), and its
  [remediation status](docs/reviews/2026-10-01-remediation-status.md)
- [Threat model](docs/threat-model.md)
- [MCP tool reference](docs/mcp-tools.md)
- [Minikube deployment](docs/runbooks/minikube.md)
- [MCP client setup](docs/runbooks/client-setup.md)
- [Cloudflare Tunnel and Managed OAuth](docs/runbooks/cloudflare-access.md)
- [Token rotation](docs/runbooks/tokens.md)
- [Backup and disaster recovery](docs/runbooks/backup-restore.md)
- [Database migrations](docs/runbooks/migrations.md)
- [Embedding model migration](docs/runbooks/embeddings.md)
- [Troubleshooting](docs/runbooks/troubleshooting.md)

Security reports belong in [SECURITY.md](SECURITY.md). Development and release expectations are in
[CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).

## Community and releases

- [Contributing guide](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Support policy](SUPPORT.md)
- [Security policy](SECURITY.md)
- [Release process and artifact verification](RELEASING.md)
- [Changelog](CHANGELOG.md)

Tagged releases publish signed application and backup images to GHCR with SBOM and provenance
attestations, plus a packaged Helm chart. Production deployments must use immutable image digests.

## MCP and hosted-client references

The standard `search` and `fetch` schemas follow the OpenAI deep-research MCP compatibility
contract. In the reference profile, hosted clients connect through Cloudflare Access Managed OAuth
and private clients connect over LAN/WireGuard with scoped device bearer tokens. The chart includes
an optional hardened Cloudflared deployment, requires an operator-supplied immutable image digest,
and keeps the origin Service private. Operators can substitute another transport only with an
equivalent, independently validated trust boundary.

- <https://developers.openai.com/api/docs/mcp>
- <https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/managed-oauth/>
- <https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/deployment-guides/kubernetes/>

## License

Maintained by [kimonus](https://github.com/kimonus).

Apache-2.0. See [LICENSE](LICENSE).
