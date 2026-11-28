"""Explicit, serialized standalone migrations; never called by application startup."""

import sys

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from knowledge_vault.config import Settings

# PostgreSQL's two-int advisory namespace is disjoint from the bigint namespace used by
# knowledge maintenance. Only participants that take this lock are serialized.
LOCK_NAMESPACE = 0x4B565343
LOCK_MIGRATION = 0x4D494752


class MigrationUnavailable(Exception):
    """The explicit standalone migration could not safely complete."""


def migrate(database_url: str, *, config_path: str = "alembic.ini") -> None:
    """Upgrade on the session holding the lock, and verify the shipped single head."""
    config = Config(config_path)
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise MigrationUnavailable("migration configuration must have exactly one head")
    url = make_url(database_url)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    if url.drivername != "postgresql+psycopg":
        raise MigrationUnavailable("migration requires PostgreSQL with psycopg")
    engine = create_engine(url, poolclass=NullPool, hide_parameters=True)
    try:
        with engine.connect() as connection:
            acquired = connection.execute(
                text("SELECT pg_try_advisory_lock(:namespace, :operation)"),
                {"namespace": LOCK_NAMESPACE, "operation": LOCK_MIGRATION},
            ).scalar_one()
            # The session lock survives this commit; Alembic must own its transaction.
            connection.commit()
            if not acquired:
                raise MigrationUnavailable("another standalone migration is running")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            actual = MigrationContext.configure(connection).get_current_heads()
            if actual != (heads[0],):
                raise MigrationUnavailable("database revision does not match the shipped head")
            connection.commit()
            unlocked = connection.execute(
                text("SELECT pg_advisory_unlock(:namespace, :operation)"),
                {"namespace": LOCK_NAMESPACE, "operation": LOCK_MIGRATION},
            ).scalar_one()
            connection.commit()
            if not unlocked:
                raise MigrationUnavailable("migration lock was not held")
    finally:
        # NullPool closes the physical session on connection exit, including every failure path.
        engine.dispose()


def run_migration() -> None:
    """CLI entry point; never print exception messages containing SQL or credentials."""
    failed = False
    try:
        migrate(Settings().database_url)
    except Exception:
        failed = True
    if failed:
        print(
            "Standalone migration failed; check database access and migration lock.",
            file=sys.stderr,
        )
        # Do not retain a credential-bearing exception in the exit exception's context.
        raise SystemExit(1)
