#!/bin/bash
# Credentials arrive only on stdin; scratch is a fresh, private, bounded tmpfs.
set -euo pipefail
umask 077
unset PGHOST PGHOSTADDR PGPORT PGUSER PGDATABASE PGPASSWORD PGOPTIONS PGSERVICE
tar -xf - --no-same-owner -C /scratch
export PGPASSFILE=/scratch/pgpass
export RESTIC_PASSWORD_FILE=/scratch/restic-password
export RESTIC_REPOSITORY=/repository
export RESTIC_CACHE_DIR=/scratch/cache
case "$1" in
  init) restic init >/dev/null ;;
  backup)
    pg_dump -h db -U knowledge_vault -d knowledge_vault --format=custom \
      --no-owner --no-privileges --file=/scratch/knowledge-vault.dump
    cd /scratch
    # Preserve the receipt HMAC key separately from SQL, encrypted by restic.
    restic backup --json --host standalone --tag standalone-postgresql knowledge-vault.dump recovery-pepper
    restic check --read-data >/dev/null
    psql -h db -U knowledge_vault -d knowledge_vault --no-psqlrc --quiet \
      --set=ON_ERROR_STOP=1 --command="
      INSERT INTO operational_heartbeats (name, succeeded_at) VALUES ('backup', now())
      ON CONFLICT (name) DO UPDATE SET succeeded_at = EXCLUDED.succeeded_at, detail = NULL" \
      >/dev/null
    ;;
  restore)
    # No --clean, no arbitrary database address, no restore to an existing instance.
    restic --no-lock check --read-data >/dev/null
    restic --no-lock dump "$2" /knowledge-vault.dump > /scratch/knowledge-vault.dump
    pg_restore -h db -U knowledge_vault -d knowledge_vault --exit-on-error \
      --single-transaction --no-owner --no-privileges /scratch/knowledge-vault.dump
    ;;
  recovery-pepper)
    # Captured privately by the controller; never relayed to terminal or logs.
    restic --no-lock dump "$2" /recovery-pepper
    ;;
  *) exit 2 ;;
esac
