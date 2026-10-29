"""Upgrade real legacy rows and restore every new relation into a second database."""

import hashlib
import subprocess
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql

pytestmark = pytest.mark.integration


def test_legacy_upgrade_and_logical_restore(postgres_url, monkeypatch):
    # This database belongs to the disposable test container, never the operator's vault.
    admin_url = postgres_url.replace("postgresql+psycopg://", "postgresql://")
    suffix = uuid4().hex[:12]
    names = ["kv_upgrade_" + suffix, "kv_restore_" + suffix]
    urls = [admin_url.rsplit("/", 1)[0] + "/" + name for name in names]
    with psycopg.connect(admin_url, autocommit=True) as admin:
        for name in names:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    cfg = Config("alembic.ini")
    monkeypatch.setenv("KNOWLEDGE_VAULT_DATABASE_URL", urls[0])
    monkeypatch.setenv("KNOWLEDGE_VAULT_ENVIRONMENT", "test")
    ident, context_id = uuid4(), uuid4()
    try:
        command.upgrade(cfg, "0004_text_artifacts")
        with psycopg.connect(urls[0]) as connection:
            original = "Synthetic legacy observation preserved exactly."
            normalized = original.casefold()
            connection.execute(
                """
                INSERT INTO assertions (id, content, normalized_content, content_hash, kind, origin,
                  status, confidence, topics, sensitivity, confirmation_count, embedding_state)
                VALUES (%s,%s,%s,%s,'configuration','user','current',1.0,ARRAY['legacy'],
                        'normal',1,'disabled')
            """,
                (ident, original, normalized, hashlib.sha256(normalized.encode()).hexdigest()),
            )
        command.upgrade(cfg, "head")
        command.check(cfg)
        with psycopg.connect(urls[0]) as connection:
            row = connection.execute(
                "SELECT content, evidence_state, revision, observed_at FROM assertions WHERE id=%s",
                (ident,),
            ).fetchone()
            assert row == (original, "unknown", 1, None)
            # Legacy writers know none of the added columns; the trigger still bumps revision.
            connection.execute("UPDATE assertions SET confidence=0.8 WHERE id=%s", (ident,))
            assert connection.execute(
                "SELECT revision FROM assertions WHERE id=%s", (ident,)
            ).fetchone() == (2,)
            connection.execute(
                "INSERT INTO knowledge_contexts VALUES (%s,'Synthetic context',ARRAY['test'])",
                (context_id,),
            )
            connection.execute(
                "UPDATE assertions SET context_id=%s, facet='test.value', "
                'provenance=\'[{"kind":"command","locator":"observation:test"}]\' '
                "WHERE id=%s",
                (context_id, ident),
            )
            connection.execute(
                "INSERT INTO assertion_usage(assertion_id,retrieval_count) VALUES (%s,7)", (ident,)
            )
            connection.execute("INSERT INTO topic_aliases VALUES ('test-alias','legacy')")

        # Run the matching pg_dump/restore clients in the same pinned image as the DB. The dump
        # contains synthetic data only and stays in memory, never in CI artifact uploads.
        from conftest import POSTGRES_IMAGE

        base = ["docker", "run", "--rm", "-i", "--network", "host", POSTGRES_IMAGE]
        dumped = subprocess.run(  # noqa: S603 - pinned image and disposable fixture URLs
            [*base, "pg_dump", "-Fc", "--no-owner", urls[0]],
            check=True,
            capture_output=True,
            timeout=60,
        ).stdout
        subprocess.run(  # noqa: S603 - pinned image and disposable fixture URLs
            [*base, "pg_restore", "--no-owner", "--no-privileges", "-d", urls[1]],
            input=dumped,
            check=True,
            capture_output=True,
            timeout=60,
        )
        with psycopg.connect(urls[1]) as restored:
            assert restored.execute(
                "SELECT content, confidence FROM assertions WHERE id=%s", (ident,)
            ).fetchone() == (original, 0.8)
            assert restored.execute(
                "SELECT retrieval_count FROM assertion_usage WHERE assertion_id=%s", (ident,)
            ).fetchone() == (7,)
            assert restored.execute(
                "SELECT name FROM knowledge_contexts WHERE id=%s", (context_id,)
            ).fetchone() == ("Synthetic context",)
            assert restored.execute(
                "SELECT canonical FROM topic_aliases WHERE alias='test-alias'"
            ).fetchone() == ("legacy",)
            # Application rollback retains expanded schema and legacy reads/writes.
            restored.execute("UPDATE assertions SET confidence=0.7 WHERE id=%s", (ident,))
        # Explicit destructive schema rollback is rehearsed only on synthetic disposable data.
        monkeypatch.setenv("KNOWLEDGE_VAULT_DATABASE_URL", urls[1])
        command.downgrade(cfg, "0004_text_artifacts")
        command.upgrade(cfg, "head")
        with psycopg.connect(urls[1]) as restored:
            assert restored.execute(
                "SELECT content,evidence_state FROM assertions WHERE id=%s", (ident,)
            ).fetchone() == (original, "unknown")
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            for name in names:
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
