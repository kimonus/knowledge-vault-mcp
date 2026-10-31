"""Migration 0006 preserves legacy values, null chronology and correction references."""

import hashlib
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.integration


def test_0006_preserves_legacy_rows_and_supports_recovery(postgres_url, monkeypatch):
    admin_url = postgres_url.replace("postgresql+psycopg://", "postgresql://")
    name = "kv_temporal_upgrade_" + uuid4().hex[:10]
    url = admin_url.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    monkeypatch.setenv("KNOWLEDGE_VAULT_DATABASE_URL", url)
    monkeypatch.setenv("KNOWLEDGE_VAULT_ENVIRONMENT", "test")
    cfg = Config("alembic.ini")
    old_id, correction_id = uuid4(), uuid4()
    try:
        command.upgrade(cfg, "0005_knowledge_maintenance")
        with psycopg.connect(url) as connection:
            for ident, status, predecessor, observed in [
                (old_id, "superseded", None, None),
                (correction_id, "current", old_id, "2026-01-31T00:00:00Z"),
            ]:
                content = "Synthetic legacy claim " + str(ident)
                connection.execute(
                    """
                    INSERT INTO assertions(id,content,normalized_content,content_hash,kind,origin,
                      status,confidence,topics,sensitivity,confirmation_count,embedding_state,
                      supersedes_id,observed_at,valid_from,valid_to,provenance,
                      created_at,updated_at,last_confirmed_at)
                    VALUES (%s,%s,%s,%s,'configuration','user',%s,0.7,ARRAY['legacy'],'normal',
                      3,'disabled',%s,%s,'2025-12-01Z','2026-03-01Z',%s,
                      '2026-03-10Z','2026-03-11Z','2026-03-12Z')
                """,
                    (
                        ident,
                        content,
                        content.casefold(),
                        hashlib.sha256(content.encode()).hexdigest(),
                        status,
                        predecessor,
                        observed,
                        Jsonb([{"kind": "conversation", "locator": "source:legacy"}]),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO assertion_sources(id,assertion_id,url,title,publisher,retrieved_at)
                    VALUES (%s,%s,'https://example.com/source','Legacy source',
                      'Example','2026-02-01Z')
                """,
                    (uuid4(), ident),
                )
            before = connection.execute(
                "SELECT row_to_json(a) FROM assertions a ORDER BY id"
            ).fetchall()
            sources = connection.execute(
                "SELECT row_to_json(s) FROM assertion_sources s ORDER BY id"
            ).fetchall()
        command.upgrade(cfg, "head")
        command.upgrade(cfg, "head")
        command.check(cfg)
        with psycopg.connect(url) as connection:
            assert (
                connection.execute("SELECT row_to_json(a) FROM assertions a ORDER BY id").fetchall()
                == before
            )
            assert (
                connection.execute(
                    "SELECT row_to_json(s) FROM assertion_sources s ORDER BY id"
                ).fetchall()
                == sources
            )
            assert connection.execute("SELECT count(*) FROM temporal_evidence").fetchone() == (0,)
            assert connection.execute("SELECT count(*) FROM temporal_retirements").fetchone() == (
                0,
            )
        command.downgrade(cfg, "0005_knowledge_maintenance")
        with psycopg.connect(url) as connection:
            assert (
                connection.execute("SELECT row_to_json(a) FROM assertions a ORDER BY id").fetchall()
                == before
            )
        command.upgrade(cfg, "head")
        command.check(cfg)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )
