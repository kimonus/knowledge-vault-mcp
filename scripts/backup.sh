#!/bin/sh
set -eu

: "${DATABASE_URL:?DATABASE_URL must be supplied by Secret}"
: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY must be supplied by Secret}"
: "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE must point to a mounted Secret file}"

backup_dir="$(mktemp -d)"
trap 'rm -rf "${backup_dir}"' EXIT INT TERM
dump_path="${backup_dir}/knowledge-vault.dump"

# The container root filesystem is read-only; keep the restic cache on the writable scratch volume.
RESTIC_CACHE_DIR="${RESTIC_CACHE_DIR:-${backup_dir}/restic-cache}"
export RESTIC_CACHE_DIR

# Snapshots are identified by a stable host and tag. The dump directory and the Pod hostname differ
# on every run, so retention must not group by path or by the container hostname.
backup_host="${BACKUP_HOST:-knowledge-vault}"
backup_tag="knowledge-vault-postgresql"

pg_dump --dbname="${DATABASE_URL}" --format=custom --no-owner --no-privileges --file="${dump_path}"
(
  cd "${backup_dir}"
  restic backup knowledge-vault.dump --host "${backup_host}" --tag "${backup_tag}"
)
restic forget --tag "${backup_tag}" --group-by tags \
  --keep-daily "${BACKUP_KEEP_DAILY:-7}" \
  --keep-weekly "${BACKUP_KEEP_WEEKLY:-4}" \
  --keep-monthly "${BACKUP_KEEP_MONTHLY:-6}" --prune
restic check --read-data-subset="${BACKUP_CHECK_SUBSET:-5%}"

# Record the success so the watchdog and the statistics tool can tell when backups stop. A
# database that predates the heartbeat table still gets its backup; only the record is skipped.
psql --dbname="${DATABASE_URL}" --quiet --no-psqlrc --set=ON_ERROR_STOP=1 --command="
  INSERT INTO operational_heartbeats (name, succeeded_at) VALUES ('backup', now())
  ON CONFLICT (name) DO UPDATE SET succeeded_at = EXCLUDED.succeeded_at, detail = NULL" \
  || echo "warning: could not record the backup heartbeat" >&2
