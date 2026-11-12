import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from knowledge_vault.api.app import create_app
from knowledge_vault.api.middleware import SchemaGuardMiddleware
from knowledge_vault.config import Settings
from knowledge_vault.container import build_container
from knowledge_vault.persistence.schema_guard import SchemaGuard
from knowledge_vault.worker.runner import run_worker_loop


async def test_schema_guard_revisions_timeout_and_cancellation(monkeypatch):
    sessions = MagicMock()
    session = sessions.return_value.__aenter__.return_value
    session.execute = AsyncMock()
    guard = SchemaGuard(sessions)
    assert guard._expected is not None
    result = MagicMock()
    session.execute.return_value = result
    for revisions, expected in [
        ([guard._expected], True),
        ([], False),
        (["stale"], False),
        (["future"], False),
        ([guard._expected, "extra"], False),
    ]:
        result.scalars.return_value.all.return_value = revisions
        assert await guard.ready() is expected
    session.execute.side_effect = RuntimeError("secret")
    assert not await guard.ready()
    session.execute.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await guard.ready()
    session.execute.side_effect = None

    async def slow():
        await asyncio.sleep(1)
        return True

    monkeypatch.setattr(guard, "_matches", slow)
    guard._timeout = 0.001
    assert not await guard.ready()
    monkeypatch.setattr(guard, "_matches", AsyncMock(return_value=True))
    assert await guard.ready()


async def test_schema_guard_saturation_and_cancellation_release_token(monkeypatch):
    guard = SchemaGuard(MagicMock(), max_concurrent_checks=1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def check():
        entered.set()
        await release.wait()
        return True

    monkeypatch.setattr(guard, "_matches", check)
    first = asyncio.create_task(guard.ready())
    await entered.wait()
    assert not await guard.ready()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await guard.ready()


async def test_schema_guard_failure_releases_token(monkeypatch):
    guard = SchemaGuard(MagicMock(), max_concurrent_checks=1)
    monkeypatch.setattr(guard, "_matches", AsyncMock(side_effect=RuntimeError("secret")))
    assert not await guard.ready()
    monkeypatch.setattr(guard, "_matches", AsyncMock(return_value=True))
    assert await guard.ready()


def test_missing_or_ambiguous_migration_assets_fail_closed(monkeypatch):
    from knowledge_vault.persistence import schema_guard

    script = MagicMock()
    monkeypatch.setattr(schema_guard.ScriptDirectory, "from_config", lambda _: script)
    for heads in ([], ["one", "two"]):
        script.get_heads.return_value = heads
        assert SchemaGuard(MagicMock())._expected is None
    script.get_heads.side_effect = OSError("unavailable")
    assert SchemaGuard(MagicMock())._expected is None


async def test_worker_waits_for_recovery_and_avoids_stale_shutdown():
    stopping = asyncio.Event()
    worker = MagicMock()
    worker.process_once = AsyncMock(return_value=0)
    worker.release_claims = AsyncMock(return_value=0)
    maintenance = AsyncMock()
    checks = 0

    async def ready():
        nonlocal checks
        checks += 1
        if checks == 1:
            assert maintenance.await_count == worker.process_once.await_count == 0
            return False
        if checks == 2:
            stopping.set()
            return True
        return False

    await run_worker_loop(
        worker,
        maintenance,
        stopping,
        poll_seconds=0.001,
        maintenance_interval_seconds=100,
        schema_ready=ready,
    )
    maintenance.assert_awaited_once()
    worker.process_once.assert_awaited_once()
    worker.release_claims.assert_not_awaited()


@pytest.mark.parametrize("path", ["/mcp", "/api/v1/search", "/metrics"])
async def test_middleware_blocks_requests_and_preflight_until_recovery(path):
    sent = []
    app = AsyncMock()
    ready = AsyncMock(return_value=False)
    middleware = SchemaGuardMiddleware(app, ready)
    scope = {"type": "http", "path": path, "method": "OPTIONS", "headers": []}

    async def send(message):
        sent.append(message)

    await middleware(scope, AsyncMock(), send)
    assert sent[0]["status"] == 503
    app.assert_not_awaited()
    ready.return_value = True
    await middleware(scope, AsyncMock(), send)
    app.assert_awaited_once()


async def test_middleware_health_bypasses_check():
    app = AsyncMock()
    ready = AsyncMock(return_value=False)
    middleware = SchemaGuardMiddleware(app, ready)
    await middleware({"type": "http", "path": "/health/ready"}, AsyncMock(), AsyncMock())
    app.assert_awaited_once()
    ready.assert_not_awaited()


async def test_standalone_app_readiness_and_recovery(monkeypatch):
    ready = AsyncMock(return_value=False)
    monkeypatch.setattr(SchemaGuard, "ready", ready)
    settings = Settings(
        environment="test",
        standalone_mode=True,
        standalone_allowed_hosts="localhost",
        embeddings_enabled=False,
    )
    container = build_container(settings)
    app = create_app(container)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        assert (await client.get("/health/live")).status_code == 200
        assert (await client.get("/health/ready")).status_code == 503
        assert (await client.post("/mcp")).status_code == 503
        assert (await client.get("/api/v1/statistics")).status_code == 503
        assert (await client.get("/mcp", headers={"host": "evil.example"})).status_code == 400
        ready.return_value = True
        assert (await client.get("/health/ready")).status_code == 200
        assert (await client.get("/api/v1/statistics")).status_code in {401, 403}
    await container.database.close()
