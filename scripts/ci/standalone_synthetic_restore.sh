#!/bin/sh
# Feasibility only: isolated synthetic data on a disposable GitHub-hosted runner.
# Operator restore remains disabled; no production, cleanup, deletion or prune operations.
set -eu
if [ "${GITHUB_ACTIONS:-}" != true ] || [ "${RUNNER_ENVIRONMENT:-}" != github-hosted ]; then
    echo 'This rehearsal requires a disposable GitHub-hosted runner.' >&2
    exit 2
fi
root=$(git rev-parse --show-toplevel)
work=$(mktemp -d "${RUNNER_TEMP:?}/kv-synthetic-restore.XXXXXXXX")
export KV_REHEARSAL_DIR="$work"
id="kvrestore${GITHUB_RUN_ID:?}${GITHUB_RUN_ATTEMPT:?}$$"
image="standalone-restore-ci:$id"
network="${id}-network"
source_db="${id}-source"
target_db="${id}-target"
db_image=pgvector/pgvector@sha256:137f044b0efe3d57f39b972b9b53641b1f2045b99d879e298bbf514a25787dcf
mkdir "$work/secrets" "$work/repository" "$work/scratch" "$work/check-scratch"
# Permissive repository/scratch permissions are solely for these synthetic disposable fixtures.
chmod 755 "$work" "$work/secrets"
chmod 777 "$work/repository" "$work/scratch" "$work/check-scratch"
python3 - <<'PY'
import os
import secrets
from pathlib import Path

path = Path(os.environ['KV_REHEARSAL_DIR']) / 'secrets'
source_password = secrets.token_urlsafe(32)
target_password = secrets.token_urlsafe(32)
(path / 'source-password').write_text(source_password)
(path / 'target-password').write_text(target_password)
(path / 'pgpass').write_text(
    'source-db:5432:knowledge_vault:knowledge_vault:' + source_password + '\n'
    + 'target-db:5432:knowledge_vault:knowledge_vault:' + target_password + '\n'
)
(path / 'pgservice').write_text(
    '[backup]\nhost=source-db\nport=5432\ndbname=knowledge_vault\nuser=knowledge_vault\n'
    '[restore]\nhost=target-db\nport=5432\ndbname=knowledge_vault\nuser=knowledge_vault\n'
)
(path / 'restic-password').write_text(secrets.token_urlsafe(32))
PY
chmod 600 "$work/secrets/pgpass"
sudo chown 999:999 "$work/secrets/pgpass"
chmod 644 "$work/secrets/source-password" "$work/secrets/target-password" \
  "$work/secrets/pgservice" "$work/secrets/restic-password"
docker build -f "$root/Dockerfile.backup" -t "$image" "$root"
docker pull "$db_image"
docker network create --internal "$network" >/dev/null
for side in source target; do
    case "$side" in
        source) container=$source_db; alias=source-db ;;
        target) container=$target_db; alias=target-db ;;
    esac
    docker run -d --name "$container" --network "$network" --network-alias "$alias" \
      --env POSTGRES_USER=knowledge_vault --env POSTGRES_DB=knowledge_vault \
      --env POSTGRES_PASSWORD_FILE=/run/fixture/postgres-password \
      --mount "type=bind,src=$work/secrets/$side-password,dst=/run/fixture/postgres-password,readonly" \
      "$db_image" >/dev/null
    attempt=0
    until docker exec "$container" pg_isready -h 127.0.0.1 -U knowledge_vault -d knowledge_vault >/dev/null 2>&1; do
        attempt=$((attempt + 1))
        if [ "$attempt" -ge 60 ]; then echo 'Synthetic database did not become ready.' >&2; exit 1; fi
        sleep 2
    done
done
docker exec "$source_db" psql -U knowledge_vault -d knowledge_vault --no-psqlrc --set=ON_ERROR_STOP=1 \
  --command="CREATE TABLE rehearsal_probe (id integer PRIMARY KEY, marker text NOT NULL); INSERT INTO rehearsal_probe VALUES (1, 'synthetic');" >/dev/null
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
# The repository was freshly created for this rehearsal and has just one trusted synthetic snapshot.
docker run --network "$network" --read-only --tmpfs /tmp:rw,nosuid,nodev,mode=1777 --cap-drop ALL --security-opt no-new-privileges \
  --mount "type=bind,src=$work/repository,dst=/repository,readonly" \
  --mount "type=bind,src=$work/check-scratch,dst=/scratch" \
  --mount "type=bind,src=$work/secrets/pgservice,dst=/run/fixture/pgservice,readonly" \
  --mount "type=bind,src=$work/secrets/pgpass,dst=/run/fixture/pgpass,readonly" \
  --mount "type=bind,src=$work/secrets/restic-password,dst=/run/fixture/restic-password,readonly" \
  --env PGSERVICEFILE=/run/fixture/pgservice --env PGPASSFILE=/run/fixture/pgpass \
  --env RESTIC_REPOSITORY=/repository --env RESTIC_PASSWORD_FILE=/run/fixture/restic-password \
  --env RESTIC_CACHE_DIR=/scratch/cache --entrypoint /bin/bash "$image" -o pipefail -c \
  'restic --no-lock dump latest /knowledge-vault.dump | pg_restore --dbname=service=restore --exit-on-error --no-owner --no-privileges'
result=$(docker exec "$target_db" psql -U knowledge_vault -d knowledge_vault --no-psqlrc --tuples-only --no-align --set=ON_ERROR_STOP=1 \
  --command="SELECT count(*) FROM rehearsal_probe WHERE id = 1 AND marker = 'synthetic'")
if [ "$result" != 1 ]; then echo 'Synthetic restore verification failed.' >&2; exit 1; fi
echo 'Synthetic restore feasibility passed. Operator restore remains disabled; full recovery is unverified.'
