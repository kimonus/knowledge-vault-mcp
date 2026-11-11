"""Standalone lock behavior against the disposable pgvector test container only."""

from uuid import uuid4

import psycopg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg import sql
from sqlalchemy.engine import make_url

from knowledge_vault import migration
from knowledge_vault.config import Settings

pytestmark = pytest.mark.integration


def test_empty_disposable_database_migrates_using_database_url_file(
    postgres_url: str, monkeypatch, tmp_path
) -> None:
    database = "kv_standalone_" + uuid4().hex[:12]
    with psycopg.connect(postgres_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    # This database belongs to the disposable test container, not an existing vault.
    url = make_url(postgres_url).set(database=database).render_as_string(hide_password=False)
    file = tmp_path / "database-url"
    file.write_text(url)
    monkeypatch.delenv("KNOWLEDGE_VAULT_DATABASE_URL", raising=False)
    monkeypatch.setenv("KNOWLEDGE_VAULT_DATABASE_URL_FILE", str(file))
    migration.migrate(Settings(environment="test").database_url)
    with psycopg.connect(url) as connection:
        rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert rows == [(heads[0],)]


def test_explicit_migration_is_repeatable_and_respects_existing_lock(postgres_url: str) -> None:
    migration.migrate(postgres_url)
    migration.migrate(postgres_url)
    with psycopg.connect(postgres_url, autocommit=True) as holder:
        holder.execute(
            "SELECT pg_advisory_lock(%s, %s)",
            (migration.LOCK_NAMESPACE, migration.LOCK_MIGRATION),
        )
        with pytest.raises(migration.MigrationUnavailable, match="another standalone migration"):
            migration.migrate(postgres_url)
    # Closing the holder releases the session lock, and a subsequent run is possible.
    migration.migrate(postgres_url)
    with psycopg.connect(postgres_url) as connection:
        rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert rows == [(heads[0],)]


def test_failed_upgrade_releases_session_lock(postgres_url: str, monkeypatch) -> None:
    def fail(_config, _revision):
        raise RuntimeError("synthetic failure")

    with monkeypatch.context() as patch:
        patch.setattr(migration.command, "upgrade", fail)
        with pytest.raises(RuntimeError, match="synthetic failure"):
            migration.migrate(postgres_url)
    with psycopg.connect(postgres_url, autocommit=True) as connection:
        assert connection.execute(
            "SELECT pg_try_advisory_lock(%s, %s)",
            (migration.LOCK_NAMESPACE, migration.LOCK_MIGRATION),
        ).fetchone() == (True,)
    migration.migrate(postgres_url)
