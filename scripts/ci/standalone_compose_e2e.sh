#!/bin/sh
# Only run in a disposable GitHub-hosted runner; fixture data is left for runner disposal.
set -eu
if [ "${GITHUB_ACTIONS:-}" != true ] || [ "${RUNNER_ENVIRONMENT:-}" != github-hosted ]; then
    echo 'This test requires a disposable GitHub-hosted runner.' >&2
    exit 2
fi
root=$(git rev-parse --show-toplevel)
tmp=$(mktemp -d "${RUNNER_TEMP:?}/kv-compose-e2e.XXXXXXXX")
project="kvci${GITHUB_RUN_ID:?}${GITHUB_RUN_ATTEMPT:?}$$"
export KV_SECRETS_DIR="$tmp/secrets" KV_TEST_SECRETS_DIR="$tmp/secrets"
export KV_APP_IMAGE="kv-compose-ci:${GITHUB_RUN_ID}"
# CI candidate only: publisher index with amd64/arm64; runtime verification is required.
export KV_GATEWAY_IMAGE='ghcr.io/nginxinc/nginx-unprivileged@sha256:8ff43bb0bd15a960d4cce6bcdfabf97c6aeb29067e3ec401f5d0eb77e66d4f8c'
compose() { docker compose -p "$project" -f "$root/deploy/standalone/compose.yaml" "$@"; }
wait_gateway() {
    attempt=0
    until curl --silent --show-error --cacert "$KV_SECRETS_DIR/tls-cert.pem" \
        --output /dev/null https://localhost:8443/ 2>/dev/null; do
        attempt=$((attempt + 1))
        if [ "$attempt" -ge 30 ]; then
            echo 'TLS gateway did not become reachable; diagnostic TLS request follows.' >&2
            curl --silent --show-error --cacert "$KV_SECRETS_DIR/tls-cert.pem" \
                --output /dev/null https://localhost:8443/ || :
            gateway=$(compose ps -q gateway)
            if [ -n "$gateway" ]; then
                docker inspect --format 'gateway state={{.State.Status}} exit={{.State.ExitCode}} restarts={{.RestartCount}}' "$gateway" || :
                docker logs "$gateway" || :
            fi
            exit 1
        fi
        sleep 2
    done
}
mkdir -m 755 "$KV_SECRETS_DIR"
chmod 755 "$tmp"
python3 - <<'PY'
import hashlib
import hmac
import json
import os
from pathlib import Path

root = Path(os.environ['KV_SECRETS_DIR'])
pepper = 'ci-fixture-pepper-not-a-real-secret'
tokens = {'writer': ('ci-fixture-writer-token', ['knowledge:read', 'knowledge:write']),
          'reader': ('ci-fixture-reader-token', ['knowledge:read'])}
records = []
for name, (token, scopes) in tokens.items():
    (root / f'{name}-token').write_text(token)
    records.append({'principal_id': name, 'sha256': hmac.new(
        pepper.encode(), token.encode(), hashlib.sha256).hexdigest(), 'scopes': scopes})
(root / 'token-pepper').write_text(pepper)
(root / 'bootstrap-tokens').write_text(json.dumps(records))
(root / 'postgres-password').write_text('ci-fixture-database-password')
(root / 'database-url').write_text(
    'postgresql://knowledge_vault:ci-fixture-database-password@db:5432/knowledge_vault')
PY
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
    -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' \
    -keyout "$KV_SECRETS_DIR/tls-key.pem" -out "$KV_SECRETS_DIR/tls-cert.pem" >/dev/null 2>&1
# These are throwaway fixture credentials on an isolated disposable runner only.
chmod 644 "$KV_SECRETS_DIR"/*
docker build --pull=false -t "$KV_APP_IMAGE" "$root"
compose up -d db
db=$(compose ps -q db)
test -n "$db"
attempt=0
until [ "$(docker inspect --format '{{.State.Health.Status}}' "$db")" = healthy ]; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then echo 'Database did not become healthy' >&2; exit 1; fi
    sleep 2
done
compose up -d api
api=$(compose ps -q api)
test -n "$api"
attempt=0
until docker exec "$api" python -c "import urllib.error,urllib.request; exec('try:\n urllib.request.urlopen(\"http://127.0.0.1:8000/health/ready\")\nexcept urllib.error.HTTPError as e:\n assert e.code == 503\nelse:\n raise AssertionError(\"unmigrated schema was ready\")')"; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then echo 'API did not fail closed before migration' >&2; exit 1; fi
    sleep 2
done
compose --profile migration run --no-deps migrate
compose up -d api worker
attempt=0
until [ "$(docker inspect --format '{{.State.Health.Status}}' "$api")" = healthy ]; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then echo 'API did not become healthy after migration' >&2; exit 1; fi
    sleep 2
done
compose up -d gateway
wait_gateway
uv run --locked python "$root/scripts/ci/standalone_gateway_client.py"
compose restart db api worker gateway
attempt=0
until [ "$(docker inspect --format '{{.State.Health.Status}}' "$api")" = healthy ]; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then echo 'API did not recover after restart' >&2; exit 1; fi
    sleep 2
done
wait_gateway
# New process: a successful read checks the database volume survived the restart.
KV_TEST_READ_ONLY=1 uv run --locked python "$root/scripts/ci/standalone_gateway_client.py"
