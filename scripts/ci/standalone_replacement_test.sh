#!/bin/sh
# Hosted-runner synthetic fixture only. Retain the old stopped containers and data volume.
set -eu
if [ "${GITHUB_ACTIONS:-}" != true ] || [ "${RUNNER_ENVIRONMENT:-}" != github-hosted ]; then
    echo 'A disposable GitHub-hosted runner is required.' >&2; exit 2
fi
project=$1
original_db=$2
root=$(git rev-parse --show-toplevel)
base="$root/deploy/standalone/compose.yaml"
volume=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' "$original_db")
if [ "$volume" != "${project}_database" ]; then
    echo 'Refusing an unexpected database volume.' >&2; exit 1
fi
for service in gateway worker api db; do
    container=$(docker compose -p "$project" -f "$base" ps -q "$service")
    if [ -z "$container" ]; then echo 'Missing synthetic fixture container.' >&2; exit 1; fi
    docker stop "$container" >/dev/null
done
override="$RUNNER_TEMP/${project}-replacement.yaml"
(
    set -C
    cat > "$override" <<EOF
services:
  gateway:
    ports: !override
      - "127.0.0.1:9443:8443"
volumes:
  database:
    external: true
    name: $volume
EOF
)
replacement="${project}replacement"
docker compose -p "$replacement" -f "$base" -f "$override" up -d db api worker gateway
replacement_db=$(docker compose -p "$replacement" -f "$base" -f "$override" ps -q db)
if [ -z "$replacement_db" ] || [ "$replacement_db" = "$original_db" ]; then
    echo 'The replacement database container was not newly created.' >&2; exit 1
fi
replacement_volume=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' "$replacement_db")
if [ "$replacement_volume" != "$volume" ]; then echo 'Unexpected replacement volume.' >&2; exit 1; fi
attempt=0
until curl --noproxy '*' -4 --silent --show-error --cacert "$KV_SECRETS_DIR/tls-cert.pem" \
    --output /dev/null https://localhost:9443/ 2>/dev/null; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then echo 'Replacement gateway unavailable.' >&2; exit 1; fi
    sleep 2
done
KV_TEST_READ_ONLY=1 KV_TEST_BASE_URL=https://localhost:9443 uv run --locked python "$root/scripts/ci/standalone_gateway_client.py"
echo 'New containers reused the synthetic database volume and retained the assertion.'
