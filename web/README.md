# Knowledge Dashboard frontend

React/TypeScript frontend for ADR 0009. Read the [Dashboard runbook](../docs/runbooks/dashboard.md)
for behavior, local HTTPS tests, independent image build, security boundaries and PR4 requirements.

The browser talks exclusively to the same-origin Dashboard BFF. All stored text is untrusted
inert content. No service bearer, database, model, LLM, browser storage or external font/CDN is
used. The frontend contract is generated from `dashboard/src/knowledge_vault_dashboard/contract.json`;
`npm run check:contract` detects drift. Use locked `npm ci`, then the verification commands in
the runbook. `npm run build` packages only production assets into the separate BFF Python package.

Tests use synthetic fixtures generated through the actual Python domain response models by
`PYTHONPATH=src .venv/bin/python web/scripts/fixtures.py` from the repository root. Never replace
these with exported personal knowledge. Browser tests additionally exercise the actual core
against disposable pgvector. No development/test fixture is packaged in the runtime image.
