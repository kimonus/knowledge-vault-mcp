# Knowledge Vault Dashboard BFF

Independent FastAPI browser boundary for core inspection protocol 1. Disabled by default;
no Explorer/Portal, public assets, image, Helm resource or deployment in PR2.
See the [runbook](../docs/runbooks/dashboard.md) and [ADR 0009](../docs/decisions/0009-optional-read-only-dashboard.md).

Build from the repository checkout with `uv build --project dashboard`. The wheel and source
distribution bundle regular copies of the linked pure auth helper source, never the core
application. The resulting distributions need no core checkout, database package or model.
Run tests against the installed wheel with `uv sync --project dashboard --locked --no-editable`,
then `cd dashboard && .venv/bin/pytest`. After changing source in an already installed non-editable
environment, add `--reinstall-package knowledge-vault-dashboard` to rebuild it.
