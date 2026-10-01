#!/bin/sh
set -eu

: "${DATABASE_URL:?DATABASE_URL must be supplied by Secret}"
: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY must be supplied by Secret}"
: "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE must point to a mounted Secret file}"

backup_dir="$(mktemp -d)"
trap 'rm -rf "${backup_dir}"' EXIT INT TERM
dump_path="${backup_dir}/knowledge-vault.dump"

pg_dump --dbname="${DATABASE_URL}" --format=custom --no-owner --no-privileges --file="${dump_path}"
(
  cd "${backup_dir}"
  restic backup knowledge-vault.dump --tag knowledge-vault-postgresql
)
restic forget --keep-daily "${BACKUP_KEEP_DAILY:-7}" \
  --keep-weekly "${BACKUP_KEEP_WEEKLY:-4}" \
  --keep-monthly "${BACKUP_KEEP_MONTHLY:-6}" --prune
restic check --read-data-subset="${BACKUP_CHECK_SUBSET:-5%}"
