#!/bin/sh
# Synthetic data on a disposable GitHub-hosted runner only. No explicit cleanup.
set -eu
if [ "${GITHUB_ACTIONS:-}" != true ] || [ "${RUNNER_ENVIRONMENT:-}" != github-hosted ]; then
    echo 'This test requires a disposable GitHub-hosted runner.' >&2
    exit 2
fi
root=$(git rev-parse --show-toplevel)
work=$(mktemp -d "${RUNNER_TEMP:?}/kv-backup-draft.XXXXXXXX")
export KV_BACKUP_TEST_DIR="$work"
id="kvbackup${GITHUB_RUN_ID:?}${GITHUB_RUN_ATTEMPT:?}$$"
image="standalone-backup-ci:$id"
db="${id}-db"
network="${id}-network"
mkdir "$work/secrets" "$work/repository" "$work/scratch" "$work/check-scratch"
# Synthetic CI fixtures only; the container image runs as UID 999. Never use these
# permissive fixture permissions for operator credentials or existing user data.
chmod 755 "$work" "$work/secrets"
chmod 777 "$work/repository" "$work/scratch" "$work/check-scratch"
python3 - <<'PY'
import os
import secrets
from pathlib import Path

path = Path(os.environ['KV_BACKUP_TEST_DIR']) / 'secrets'
password = secrets.token_urlsafe(32)
(path / 'postgres-password').write_text(password)
(path / 'pgpass').write_text('db:5432:knowledge_vault:knowledge_vault:' + password + '\n')
(path / 'pgservice').write_text('[backup]\nhost=db\nport=5432\ndbname=knowledge_vault\nuser=knowledge_vault\n')
(path / 'restic-password').write_text(secrets.token_urlsafe(32))
PY
# libpq rejects password files that are group/world readable. Container UID 999 reads
# this synthetic fixture via ownership on the isolated runner.
chmod 600 "$work/secrets/pgpass"
sudo chown 999:999 "$work/secrets/pgpass"
chmod 644 "$work/secrets/postgres-password" "$work/secrets/pgservice" "$work/secrets/restic-password"
docker build -f "$root/Dockerfile.backup" -t "$image" "$root"
docker pull pgvector/pgvector@sha256:137f044b0efe3d57f39b972b9b53641b1f2045b99d879e298bbf514a25787dcf
docker network create --internal "$network" >/dev/null
docker run -d --name "$db" --network "$network" --network-alias db \
  --env POSTGRES_USER=knowledge_vault --env POSTGRES_DB=knowledge_vault \
  --env POSTGRES_PASSWORD_FILE=/run/fixture/postgres-password \
  --mount "type=bind,src=$work/secrets/postgres-password,dst=/run/fixture/postgres-password,readonly" \
  pgvector/pgvector@sha256:137f044b0efe3d57f39b972b9b53641b1f2045b99d879e298bbf514a25787dcf >/dev/null
attempt=0
until docker exec "$db" pg_isready -h 127.0.0.1 -U knowledge_vault -d knowledge_vault >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then echo 'Synthetic database did not become ready.' >&2; exit 1; fi
    sleep 2
done
docker exec "$db" psql -U knowledge_vault -d knowledge_vault --no-psqlrc --set=ON_ERROR_STOP=1 \
  --command="CREATE TABLE backup_ci_probe (id integer PRIMARY KEY, marker text NOT NULL); INSERT INTO backup_ci_probe VALUES (1, 'synthetic');" >/dev/null
docker run --network "$network" --read-only --tmpfs /tmp:rw,nosuid,nodev,mode=1777 --cap-drop ALL --security-opt no-new-privileges \
  --mount "type=bind,src=$work/repository,dst=/repository" \
  --mount "type=bind,src=$work/check-scratch,dst=/scratch" \
  --mount "type=bind,src=$work/secrets/restic-password,dst=/run/fixture/restic-password,readonly" \
  --env RESTIC_REPOSITORY=/repository --env RESTIC_PASSWORD_FILE=/run/fixture/restic-password \
  --env RESTIC_CACHE_DIR=/scratch/cache --entrypoint restic "$image" init >/dev/null
docker run --network "$network" --read-only --tmpfs /tmp:rw,nosuid,nodev,mode=1777 --cap-drop ALL --security-opt no-new-privileges \
  --mount "type=bind,src=$work/repository,dst=/repository" \
  --mount "type=bind,src=$work/scratch,dst=/scratch" \
  --mount "type=bind,src=$work/secrets/pgservice,dst=/run/fixture/pgservice,readonly" \
  --mount "type=bind,src=$work/secrets/pgpass,dst=/run/fixture/pgpass,readonly" \
  --mount "type=bind,src=$work/secrets/restic-password,dst=/run/fixture/restic-password,readonly" \
  --mount "type=bind,src=$root/deploy/standalone/backup-safe.sh,dst=/run/backup-safe.sh,readonly" \
  --env PGSERVICEFILE=/run/fixture/pgservice --env PGPASSFILE=/run/fixture/pgpass \
  --env RESTIC_PASSWORD_FILE=/run/fixture/restic-password --env SCRATCH_DIR=/scratch \
  --entrypoint /bin/sh "$image" /run/backup-safe.sh
docker run --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,mode=1777 --cap-drop ALL --security-opt no-new-privileges \
  --mount "type=bind,src=$work/repository,dst=/repository,readonly" \
  --mount "type=bind,src=$work/check-scratch,dst=/scratch" \
  --mount "type=bind,src=$work/secrets/restic-password,dst=/run/fixture/restic-password,readonly" \
  --env RESTIC_REPOSITORY=/repository --env RESTIC_PASSWORD_FILE=/run/fixture/restic-password \
  --env RESTIC_CACHE_DIR=/scratch/cache --entrypoint restic "$image" --no-lock check --read-data
docker run --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,mode=1777 --cap-drop ALL --security-opt no-new-privileges \
  --mount "type=bind,src=$work/repository,dst=/repository,readonly" \
  --mount "type=bind,src=$work/check-scratch,dst=/scratch" \
  --mount "type=bind,src=$work/secrets/restic-password,dst=/run/fixture/restic-password,readonly" \
  --env RESTIC_REPOSITORY=/repository --env RESTIC_PASSWORD_FILE=/run/fixture/restic-password \
  --env RESTIC_CACHE_DIR=/scratch/cache --entrypoint /bin/bash "$image" -o pipefail -c \
  'restic --no-lock dump latest /knowledge-vault.dump | pg_restore --list | grep "TABLE public backup_ci_probe" > /dev/null'
echo 'Synthetic encrypted backup integrity and archive structure checks passed; restore remains disabled.'
