#!/bin/sh
# Run only in the reviewed backup image with an independent repository and private scratch mount.
set -eu
umask 077
: "${PGSERVICEFILE:?Mount a libpq service file}"
: "${PGPASSFILE:?Mount a libpq password file}"
: "${RESTIC_PASSWORD_FILE:?Mount a restic password file}"
: "${SCRATCH_DIR:?Mount a private, writable scratch directory}"
unset PGHOST PGHOSTADDR PGPORT PGUSER PGDATABASE PGPASSWORD PGOPTIONS PGSERVICE
export RESTIC_REPOSITORY=/repository
export RESTIC_CACHE_DIR="$SCRATCH_DIR/restic-cache"
dump="$SCRATCH_DIR/knowledge-vault.dump"
# Refuse an existing dump: neither failed attempts nor existing files are removed or overwritten.
(set -C; : > "$dump")
pg_dump --dbname=service=backup --format=custom --no-owner --no-privileges --file="$dump"
(cd "$SCRATCH_DIR" && restic backup knowledge-vault.dump --host standalone --tag standalone-postgresql)
echo 'Backup command completed. Verify the encrypted snapshot and retain the scratch safely.'
