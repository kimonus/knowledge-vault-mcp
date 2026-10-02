#!/bin/sh
set -eu

image="${1:-knowledge-vault-backup:ci}"
work_dir="$(mktemp -d)"
database_container="knowledge-vault-backup-test-$$"

cleanup() {
  status=$?
  trap - EXIT INT TERM
  docker rm --force "${database_container}" >/dev/null 2>&1 || true
  docker run --rm --user 0:0 \
    --volume "${work_dir}:/cleanup" \
    --entrypoint rm "${image}" \
    -rf /cleanup/repository /cleanup/restore >/dev/null 2>&1 || true
  rm -rf "${work_dir}" || true
  exit "${status}"
}
trap cleanup EXIT INT TERM

mkdir -p "${work_dir}/repository" "${work_dir}/restore" "${work_dir}/secrets"
chmod 0777 "${work_dir}"
chmod 0755 "${work_dir}/secrets"
chmod 0777 "${work_dir}/repository" "${work_dir}/restore"
printf '%s\n' 'disposable-backup-password' >"${work_dir}/secrets/password"
chmod 0444 "${work_dir}/secrets/password"

docker run --detach --name "${database_container}" \
  --env POSTGRES_USER=knowledge_vault \
  --env POSTGRES_PASSWORD=disposable-database-password \
  --env POSTGRES_DB=knowledge_vault \
  pgvector/pgvector:0.8.1-pg17-trixie@sha256:137f044b0efe3d57f39b972b9b53641b1f2045b99d879e298bbf514a25787dcf \
  >/dev/null

attempt=0
until docker exec "${database_container}" pg_isready -U knowledge_vault -d knowledge_vault \
  >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "${attempt}" -ge 60 ]; then
    echo "disposable PostgreSQL did not become ready" >&2
    exit 1
  fi
  sleep 1
done

docker exec "${database_container}" psql -v ON_ERROR_STOP=1 \
  -U knowledge_vault -d knowledge_vault \
  -c "CREATE TABLE backup_probe (id integer PRIMARY KEY, marker text NOT NULL);" \
  -c "INSERT INTO backup_probe VALUES (1, 'synthetic');" \
  -c "CREATE TABLE operational_heartbeats (name varchar(40) PRIMARY KEY, succeeded_at timestamptz NOT NULL DEFAULT now(), detail varchar(200));" \
  >/dev/null

docker run --rm \
  --volume "${work_dir}/repository:/repository" \
  --volume "${work_dir}/secrets:/secrets:ro" \
  --env RESTIC_PASSWORD_FILE=/secrets/password \
  --entrypoint restic \
  "${image}" init --repo /repository >/dev/null

# Run the job exactly as the chart does: read-only root filesystem with only /tmp writable.
# Three runs with a keep-one policy must leave one snapshot, proving that retention prunes.
run=0
while [ "${run}" -lt 3 ]; do
  run=$((run + 1))
  docker run --rm --read-only --tmpfs /tmp:rw,size=256m \
    --network "container:${database_container}" \
    --volume "${work_dir}/repository:/repository" \
    --volume "${work_dir}/secrets:/secrets:ro" \
    --env DATABASE_URL=postgresql://knowledge_vault:disposable-database-password@127.0.0.1:5432/knowledge_vault \
    --env RESTIC_REPOSITORY=/repository \
    --env RESTIC_PASSWORD_FILE=/secrets/password \
    --env BACKUP_KEEP_DAILY=1 \
    --env BACKUP_KEEP_WEEKLY=1 \
    --env BACKUP_KEEP_MONTHLY=1 \
    --env BACKUP_CHECK_SUBSET=100% \
    "${image}" >/dev/null
done

snapshot_count="$(docker run --rm \
  --volume "${work_dir}/repository:/repository" \
  --volume "${work_dir}/secrets:/secrets:ro" \
  --env RESTIC_PASSWORD_FILE=/secrets/password \
  --entrypoint restic \
  "${image}" snapshots --repo /repository --json | grep -o '"short_id"' | wc -l)"
if [ "${snapshot_count}" -ne 1 ]; then
  echo "retention kept ${snapshot_count} snapshots; expected exactly 1" >&2
  exit 1
fi

# Every successful run records a heartbeat for the watchdog.
heartbeat_count="$(docker exec "${database_container}" psql -At \
  -U knowledge_vault -d knowledge_vault \
  -c "SELECT count(*) FROM operational_heartbeats WHERE name = 'backup' AND succeeded_at > now() - interval '5 minutes';")"
test "${heartbeat_count}" = "1"

docker run --rm \
  --volume "${work_dir}/repository:/repository" \
  --volume "${work_dir}/restore:/restore" \
  --volume "${work_dir}/secrets:/secrets:ro" \
  --env RESTIC_PASSWORD_FILE=/secrets/password \
  --entrypoint restic \
  "${image}" restore latest --repo /repository --target /restore >/dev/null

dump_path="${work_dir}/restore/knowledge-vault.dump"
test -f "${dump_path}"

docker run --rm \
  --volume "${dump_path}:/restore/knowledge-vault.dump:ro" \
  --entrypoint pg_restore \
  "${image}" --list /restore/knowledge-vault.dump >/dev/null

docker exec "${database_container}" createdb \
  -U knowledge_vault knowledge_vault_restored
docker run --rm \
  --network "container:${database_container}" \
  --volume "${dump_path}:/restore/knowledge-vault.dump:ro" \
  --entrypoint pg_restore \
  "${image}" --no-owner --no-privileges \
  --dbname=postgresql://knowledge_vault:disposable-database-password@127.0.0.1:5432/knowledge_vault_restored \
  /restore/knowledge-vault.dump

row_count="$(docker exec "${database_container}" psql -At \
  -U knowledge_vault -d knowledge_vault_restored \
  -c "SELECT count(*) FROM backup_probe WHERE id = 1 AND marker = 'synthetic';")"
test "${row_count}" = "1"

echo "backup and restore smoke test passed"
