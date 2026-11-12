#!/bin/sh
set -eu

# Run from this directory, immediately after `compose up -d db`.
# The engine CLI is also used for inspect; never inspect another Compose project.
engine=${KV_COMPOSE_ENGINE:-docker}
case "$engine" in docker|podman) ;; *) echo 'KV_COMPOSE_ENGINE must be docker or podman' >&2; exit 1;; esac
db_id=$("$engine" compose ps -q db)
if [ -z "$db_id" ]; then
    echo 'Compose did not return a database container; start db first.' >&2
    exit 1
fi
attempt=0
while [ "$attempt" -lt 60 ]; do
    if ! state=$("$engine" inspect --format '{{.State.Status}} {{.State.Health.Status}}' "$db_id"); then
        echo 'Cannot inspect the database container.' >&2
        exit 1
    fi
    case "$state" in
        'running healthy') echo 'Database container reports healthy.'; exit 0 ;;
        'running starting') ;;
        *) printf 'Database container is not ready: %s\n' "$state" >&2; exit 1 ;;
    esac
    attempt=$((attempt + 1))
    sleep 2
done
echo 'Timed out waiting for database health.' >&2
exit 1
