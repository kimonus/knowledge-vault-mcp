#!/bin/sh
# Synthetic hosted-runner feasibility only. No cleanup, deletion, or operator restore.
set -eu
if [ "${GITHUB_ACTIONS:-}" != true ] || [ "${RUNNER_ENVIRONMENT:-}" != github-hosted ]; then
    echo 'A disposable GitHub-hosted runner is required.' >&2; exit 2
fi
project=$1
work=$2
app_image=$3
root=$(git rev-parse --show-toplevel)
export KV_RECOVERY_STATE="$work/recovery-state.json"
uv run --locked python "$root/scripts/ci/standalone_recovery_client.py"
mkdir "$work/repository" "$work/backup-scratch" "$work/restore-scratch" "$work/recovery-secrets"
chmod 777 "$work/repository" "$work/backup-scratch" "$work/restore-scratch"
chmod 755 "$work/recovery-secrets"
export KV_RECOVERY_WORK="$work"
python3 - <<'PY'
import os, secrets
from pathlib import Path
p = Path(os.environ['KV_RECOVERY_WORK'])
password = (p / 'secrets/postgres-password').read_text()
target = secrets.token_urlsafe(32)
d = p / 'recovery-secrets'
(d / 'target-password').write_text(target)
(d / 'database-url').write_text(f'postgresql://knowledge_vault:{target}@db:5432/knowledge_vault')
(d / 'pgpass').write_text(f'db:5432:knowledge_vault:knowledge_vault:{password}\ntarget-db:5432:knowledge_vault:knowledge_vault:{target}\n')
(d / 'pgservice').write_text('[backup]\nhost=db\nport=5432\ndbname=knowledge_vault\nuser=knowledge_vault\n[restore]\nhost=target-db\nport=5432\ndbname=knowledge_vault\nuser=knowledge_vault\n')
(d / 'restic-password').write_text(secrets.token_urlsafe(32))
PY
chmod 600 "$work/recovery-secrets/pgpass"
sudo chown 999:999 "$work/recovery-secrets/pgpass"
chmod 644 "$work/recovery-secrets/target-password" "$work/recovery-secrets/database-url" "$work/recovery-secrets/pgservice" "$work/recovery-secrets/restic-password"
backup_image="kv-app-recovery-backup:${GITHUB_RUN_ID:?}"
docker build -f "$root/Dockerfile.backup" -t "$backup_image" "$root"
docker run --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
  --mount "type=bind,src=$work/repository,dst=/repository" --mount "type=bind,src=$work/backup-scratch,dst=/scratch" \
  --mount "type=bind,src=$work/recovery-secrets/restic-password,dst=/run/fixture/restic-password,readonly" \
  --env RESTIC_REPOSITORY=/repository --env RESTIC_PASSWORD_FILE=/run/fixture/restic-password --env RESTIC_CACHE_DIR=/scratch/cache \
  --entrypoint restic "$backup_image" init >/dev/null
docker run --network "${project}_private" --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
  --mount "type=bind,src=$work/repository,dst=/repository" --mount "type=bind,src=$work/backup-scratch,dst=/scratch" \
  --mount "type=bind,src=$work/recovery-secrets/pgservice,dst=/run/fixture/pgservice,readonly" \
  --mount "type=bind,src=$work/recovery-secrets/pgpass,dst=/run/fixture/pgpass,readonly" \
  --mount "type=bind,src=$work/recovery-secrets/restic-password,dst=/run/fixture/restic-password,readonly" \
  --mount "type=bind,src=$root/deploy/standalone/backup-safe.sh,dst=/run/backup-safe.sh,readonly" \
  --env PGSERVICEFILE=/run/fixture/pgservice --env PGPASSFILE=/run/fixture/pgpass --env RESTIC_PASSWORD_FILE=/run/fixture/restic-password --env SCRATCH_DIR=/scratch \
  --entrypoint /bin/sh "$backup_image" /run/backup-safe.sh
target_network="${project}-recovery-private"
front_network="${project}-recovery-front"
docker network create --internal "$target_network" >/dev/null
docker network create "$front_network" >/dev/null
target_db="${project}-recovery-db"
docker run -d --name "$target_db" --network "$target_network" --network-alias db --network-alias target-db \
  --env POSTGRES_USER=knowledge_vault --env POSTGRES_DB=knowledge_vault --env POSTGRES_PASSWORD_FILE=/run/fixture/password \
  --mount "type=bind,src=$work/recovery-secrets/target-password,dst=/run/fixture/password,readonly" \
  pgvector/pgvector@sha256:137f044b0efe3d57f39b972b9b53641b1f2045b99d879e298bbf514a25787dcf >/dev/null
attempt=0
until docker exec "$target_db" pg_isready -U knowledge_vault -d knowledge_vault >/dev/null 2>&1; do
    attempt=$((attempt + 1)); if [ "$attempt" -ge 60 ]; then echo 'Target database not ready' >&2; exit 1; fi; sleep 2
done
docker run --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
  --mount "type=bind,src=$work/repository,dst=/repository,readonly" --mount "type=bind,src=$work/restore-scratch,dst=/scratch" \
  --mount "type=bind,src=$work/recovery-secrets/restic-password,dst=/run/fixture/restic-password,readonly" \
  --env RESTIC_REPOSITORY=/repository --env RESTIC_PASSWORD_FILE=/run/fixture/restic-password --env RESTIC_CACHE_DIR=/scratch/cache \
  --entrypoint /bin/sh "$backup_image" -c 'set -eu; set -C; restic --no-lock dump latest /knowledge-vault.dump > /scratch/restore.dump'
docker run --network "$target_network" --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
  --mount "type=bind,src=$work/restore-scratch/restore.dump,dst=/run/fixture/restore.dump,readonly" \
  --mount "type=bind,src=$work/recovery-secrets/pgservice,dst=/run/fixture/pgservice,readonly" \
  --mount "type=bind,src=$work/recovery-secrets/pgpass,dst=/run/fixture/pgpass,readonly" \
  --env PGSERVICEFILE=/run/fixture/pgservice --env PGPASSFILE=/run/fixture/pgpass \
  --entrypoint pg_restore "$backup_image" --dbname=service=restore --exit-on-error --no-owner --no-privileges /run/fixture/restore.dump
docker run -d --name "${project}-recovery-api" --network "$target_network" --network-alias api --read-only --cap-drop ALL --security-opt no-new-privileges \
  --mount "type=bind,src=$work/recovery-secrets/database-url,dst=/run/kv-secrets/database-url,readonly" \
  --mount "type=bind,src=$work/secrets/token-pepper,dst=/run/kv-secrets/token-pepper,readonly" \
  --mount "type=bind,src=$work/secrets/bootstrap-tokens,dst=/run/kv-secrets/bootstrap-tokens,readonly" \
  --env KNOWLEDGE_VAULT_DATABASE_URL_FILE=/run/kv-secrets/database-url --env KNOWLEDGE_VAULT_TOKEN_PEPPER_FILE=/run/kv-secrets/token-pepper \
  --env KNOWLEDGE_VAULT_BOOTSTRAP_TOKENS_FILE=/run/kv-secrets/bootstrap-tokens --env KNOWLEDGE_VAULT_STANDALONE_MODE=true \
  --env KNOWLEDGE_VAULT_STANDALONE_ALLOWED_HOSTS=localhost,127.0.0.1 --env KNOWLEDGE_VAULT_PUBLIC_BASE_URL=https://localhost:9443 \
  --env KNOWLEDGE_VAULT_EMBEDDINGS_ENABLED=false --env KNOWLEDGE_VAULT_EMBEDDING_ALLOW_DOWNLOAD=false "$app_image" >/dev/null
attempt=0
until docker exec "${project}-recovery-api" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)" >/dev/null 2>&1; do
    attempt=$((attempt + 1)); if [ "$attempt" -ge 60 ]; then echo 'Target API not ready' >&2; exit 1; fi; sleep 2
done
docker create --name "${project}-recovery-gateway" --network "$target_network" --user 101:101 --read-only --tmpfs /tmp:uid=101,gid=101,mode=0700 \
  --cap-drop ALL --security-opt no-new-privileges -p 127.0.0.1:9443:8443 \
  --mount "type=bind,src=$root/deploy/standalone/nginx.conf,dst=/etc/nginx/nginx.conf,readonly" \
  --mount "type=bind,src=$work/secrets/tls-cert.pem,dst=/run/kv-secrets/tls-cert.pem,readonly" \
  --mount "type=bind,src=$work/secrets/tls-key.pem,dst=/run/kv-secrets/tls-key.pem,readonly" \
  --entrypoint nginx "$KV_GATEWAY_IMAGE" -g 'daemon off; error_log /dev/null crit;' >/dev/null
docker network connect "$front_network" "${project}-recovery-gateway"
docker start "${project}-recovery-gateway" >/dev/null
attempt=0
until curl --noproxy '*' -4 --silent --show-error --cacert "$work/secrets/tls-cert.pem" --output /dev/null https://localhost:9443/ 2>/dev/null; do
    attempt=$((attempt + 1)); if [ "$attempt" -ge 60 ]; then echo 'Target TLS gateway unavailable' >&2; exit 1; fi; sleep 2
done
KV_RECOVERY_VERIFY=1 KV_RECOVERY_URL=https://localhost:9443/mcp uv run --locked python "$root/scripts/ci/standalone_recovery_client.py"
