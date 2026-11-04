#!/bin/sh
set -eu
export PATH=/usr/lib/postgresql/17/bin:/usr/bin:/bin
export PGDATA=/tmp/pgdata PGHOST=/tmp LC_ALL=C
export PYTHONPATH=/opt/site:/opt/repo/src:/opt/repo
export KNOWLEDGE_VAULT_ENVIRONMENT=test
export 'KNOWLEDGE_VAULT_DATABASE_URL=postgresql+psycopg://kvtest@/postgres?host=/tmp'
initdb --no-locale --encoding=UTF8 --auth=trust >/dev/null
cleanup() { pg_ctl -m immediate -w stop >/dev/null 2>&1 || true; }
trap cleanup EXIT HUP INT TERM
pg_ctl -o "-c listen_addresses='' -c unix_socket_directories=/tmp -c max_connections=10 -c shared_buffers=16MB" -w start >/dev/null
cd /tmp
/opt/python/bin/python3.12 -c 'from alembic import command; from alembic.config import Config; from knowledge_vault.config import Settings; Settings.model_config["env_file"] = None; c = Config("/opt/repo/alembic.ini"); c.set_main_option("script_location", "/opt/repo/migrations"); command.upgrade(c, "head")'
/opt/python/bin/python3.12 /tmp/probe.py
pg_ctl -m fast -w stop >/dev/null
trap - EXIT HUP INT TERM
