#!/bin/sh
set -eu
export PATH=/usr/lib/postgresql/17/bin:/usr/bin:/bin
export PGDATA=/tmp/pgdata PGHOST=/tmp LC_ALL=C
export PYTHONPATH=/opt/site:/opt/repo/src:/opt/repo
export KNOWLEDGE_VAULT_ENVIRONMENT=test
export 'KNOWLEDGE_VAULT_DATABASE_URL=postgresql+psycopg://kvtest@/postgres?host=/tmp'
# Every format and stage below is a literal; stderr remains discarded by the host.
# Overwrite a precreated server-only file: finite snapshots, no raw command output.
phase() {
    stage=$1 previous=$2
    shift 2
    printf 'v1 %s %s entered 0\n' "$stage" "$previous" > /tmp/startup.status
    if "$@"; then
        printf 'v1 %s %s completed 0\n' "$stage" "$stage" > /tmp/startup.status
    else
        status=$?
        printf 'v1 %s %s command_failed %s\n' "$stage" "$previous" "$status" > /tmp/startup.status
        exit "$status"
    fi
}
printf 'v1 sandbox_launch sandbox_launch completed 0\n' > /tmp/startup.status
phase initdb sandbox_launch initdb --no-locale --encoding=UTF8 --auth=trust >/dev/null
cleanup() { pg_ctl -m immediate -w stop >/dev/null 2>&1 || true; }
trap cleanup EXIT HUP INT TERM
phase pg_start initdb pg_ctl -o "-c listen_addresses='' -c unix_socket_directories=/tmp -c max_connections=10 -c shared_buffers=16MB" -w start >/dev/null
cd /tmp
phase alembic pg_start /opt/python/bin/python3.12 -c 'from alembic import command; from alembic.config import Config; from knowledge_vault.config import Settings; Settings.model_config["env_file"] = None; c = Config("/opt/repo/alembic.ini"); c.set_main_option("script_location", "/opt/repo/migrations"); command.upgrade(c, "head")'
printf 'v1 python_imports alembic entered 0\n' > /tmp/startup.status
/opt/python/bin/python3.12 /opt/repo/scripts/feasibility_support/startup.py
pg_ctl -m fast -w stop >/dev/null
trap - EXIT HUP INT TERM
