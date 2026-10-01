# Contributing

Thank you for improving Knowledge Vault. Participation is governed by
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). By contributing, you agree that your contribution is
licensed under Apache-2.0, the repository's license.

## Before opening a change

Search issues and architecture decisions first. Open an issue before substantial architecture,
protocol, authentication, persistence, or deployment changes so the design can be discussed before
implementation. Small bug fixes, tests, and documentation corrections can go directly to a pull
request.

Report vulnerabilities privately according to [SECURITY.md](SECURITY.md), never through an issue or
pull request. Never add real credentials, personal assertions, private infrastructure identifiers,
or unsanitized logs to code, fixtures, commits, or CI output.

## Development setup

Prerequisites are Python 3.12+, [uv](https://docs.astral.sh/uv/), Docker, and a running Docker
daemon. Helm and kubeconform are required for chart changes.

```bash
git clone https://github.com/kimonus/knowledge-vault-mcp.git
cd knowledge-vault-mcp
uv sync --locked
uv run pre-commit install
cp .env.example .env
```

Use only disposable credentials and synthetic assertions in development. Integration and
end-to-end tests create a disposable PostgreSQL/pgvector container through Testcontainers.

## Design expectations

Keep MCP and HTTP adapters thin and application services independent of transport request objects.
PostgreSQL remains the durable source of truth; embeddings are derived. Preserve exact idempotency,
explicit correction/supersession, bounded result sets, confirmed deletion, and common-secret
rejection. Treat retrieved knowledge as untrusted data.

Authentication and authorization belong in server code, not only annotations, ingress, or client
instructions. Logs, traces, and metrics must remain free of assertion content, excerpts, bearer
tokens, embeddings, complete MCP payloads, and SQL values containing user data.

Schema changes require Alembic migrations and database constraints. Keep migrations reversible
where feasible and document downgrade/data-loss limitations. New dependencies require a documented
need, a locked version graph, license and vulnerability review, and regenerated SBOM in CI.

## Run the gates

Start with the narrowest relevant test, then run the complete local gates before requesting review:

```bash
uv sync --locked
uv run pre-commit run --all-files
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest tests/unit tests/property tests/contract
uv run pytest tests/integration tests/e2e
uv run pytest --cov --cov-branch --cov-report=json --cov-report=term-missing
uv run python scripts/check_coverage.py coverage.json
uv run pip-audit --progress-spinner off
uv run bandit -q -r src
```

Coverage must remain at least 90% statements and 85% branches for application code. Do not weaken
tests, security checks, or exclusions to make a change pass.

For container or deployment changes, also run:

```bash
docker build --pull=false -t knowledge-vault:ci .
docker build --pull=false -f Dockerfile.backup -t knowledge-vault-backup:ci .
sh scripts/ci/test_backup_restore.sh knowledge-vault-backup:ci
helm lint charts/knowledge-vault -f charts/knowledge-vault/values-minikube.yaml
helm template knowledge-vault charts/knowledge-vault \
  -f charts/knowledge-vault/values-minikube.yaml > rendered.yaml
kubeconform -strict -summary -kubernetes-version 1.33.0 rendered.yaml
```

CI additionally performs secret, Dockerfile, image-vulnerability, SBOM, and MCP Inspector checks.
Docker-backed integration tests are mandatory for persistence changes. Backup changes must prove a
restore into a second disposable database, not only create a snapshot.

## Documentation and contracts

Update schemas, MCP contract tests, Helm values/schema, changelog, security documentation, and
runbooks in the same change as behavior or operational requirements. Add or amend an architecture
decision when a long-lived boundary changes. Keep examples generic and safe to publish.

## Commits and pull requests

Use a focused branch and keep commits reviewable. Commit subjects should be imperative and state the
behavior changed. Avoid unrelated formatting or generated artifacts.

Complete the pull-request template with risk, exact verification results, migration/rollback impact,
and operator-supplied configuration. Never claim a scanner, cluster test, backup restore, or
Inspector run that did not execute. The maintainer may request changes when tests pass but the
security, recovery, or operational argument remains incomplete.

Releases are maintainer-only and follow [RELEASING.md](RELEASING.md).
