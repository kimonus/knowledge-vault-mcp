"""Schema transitions use a private database in the disposable test container."""

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from knowledge_vault.api.app import create_app
from knowledge_vault.container import build_container
from knowledge_vault.persistence.database import Database
from knowledge_vault.persistence.schema_guard import SchemaGuard
from knowledge_vault.worker.runner import run_worker_loop

pytestmark = pytest.mark.integration


async def test_schema_guard_handles_database_transitions(postgres_url, settings):
    name = "kv_schema_" + uuid4().hex[:12]
    with psycopg.connect(postgres_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_url(postgres_url).set(database=name).render_as_string(hide_password=False)
    database = Database(url)
    guard = SchemaGuard(database.sessions)
    container = build_container(
        settings.model_copy(
            update={
                "database_url": url,
                "standalone_mode": True,
                "standalone_allowed_hosts": "localhost",
            }
        )
    )
    assert guard._expected is not None
    try:
        assert not await guard.ready()  # no version table
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(container)), base_url="http://localhost"
        ) as client:
            with psycopg.connect(url, autocommit=True) as connection:
                connection.execute(
                    "CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)"
                )
                assert not await guard.ready()
                assert (await client.get("/health/ready")).status_code == 503
                assert (await client.post("/mcp")).status_code == 503
                assert (await client.get("/api/v1/statistics")).status_code == 503
                connection.execute("INSERT INTO alembic_version VALUES ('old')")
                assert not await guard.ready()
                connection.execute("UPDATE alembic_version SET version_num = 'future'")
                assert not await guard.ready()
                connection.execute(
                    "UPDATE alembic_version SET version_num = %s", (guard._expected,)
                )
                assert await guard.ready()
                assert (await client.get("/health/ready")).status_code == 200
                assert (await client.get("/api/v1/statistics")).status_code in {401, 403}
                connection.execute("UPDATE alembic_version SET version_num = 'stale_again'")
                assert not await guard.ready()
                stop = asyncio.Event()
                maintenance = AsyncMock()
                worker = MagicMock(
                    process_once=AsyncMock(return_value=0), release_claims=AsyncMock()
                )

                async def stale_check():
                    stop.set()
                    return await guard.ready()

                await run_worker_loop(
                    worker,
                    maintenance,
                    stop,
                    poll_seconds=0.001,
                    maintenance_interval_seconds=60,
                    schema_ready=stale_check,
                )
                maintenance.assert_not_awaited()
                worker.process_once.assert_not_awaited()
                worker.release_claims.assert_not_awaited()
                connection.execute(
                    "UPDATE alembic_version SET version_num = %s", (guard._expected,)
                )
                assert await guard.ready()
                stop = asyncio.Event()

                async def recovered_maintenance():
                    stop.set()

                maintenance.side_effect = recovered_maintenance
                await run_worker_loop(
                    worker,
                    maintenance,
                    stop,
                    poll_seconds=0.001,
                    maintenance_interval_seconds=60,
                    schema_ready=guard.ready,
                )
                maintenance.assert_awaited_once()
                worker.process_once.assert_awaited_once()
                connection.execute("INSERT INTO alembic_version VALUES ('extra')")
                assert not await guard.ready()
    finally:
        await container.database.close()
        await database.close()
