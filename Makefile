UV ?= uv

.PHONY: install format lint typecheck unit integration test coverage migrate run worker docker-build helm-check plugin-check

install:
	$(UV) sync --locked

format:
	$(UV) run ruff format .

lint:
	$(UV) run ruff format --check .
	$(UV) run ruff check .

typecheck:
	$(UV) run pyright

unit:
	$(UV) run pytest tests/unit tests/property tests/contract -q

integration:
	$(UV) run pytest tests/integration tests/e2e -q

test:
	$(UV) run pytest -q

coverage:
	$(UV) run pytest --cov --cov-branch --cov-report=term-missing --cov-report=xml

migrate:
	$(UV) run alembic upgrade head

run:
	$(UV) run knowledge-vault-api

worker:
	$(UV) run knowledge-vault-worker

docker-build:
	docker build --pull=false -t knowledge-vault:local .

helm-check:
	helm lint charts/knowledge-vault --values charts/knowledge-vault/values-minikube.yaml
	helm template knowledge-vault charts/knowledge-vault --values charts/knowledge-vault/values-minikube.yaml

plugin-check:
	test -n "$${CODEX_HOME:-}" && python3 "$${CODEX_HOME}/skills/.system/plugin-creator/scripts/validate_plugin.py" plugin/knowledge-vault
