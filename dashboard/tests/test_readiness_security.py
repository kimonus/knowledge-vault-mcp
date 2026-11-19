"""Regression tests for independent review R1: anonymous probe amplification/starvation."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from knowledge_vault_dashboard import limits as limits_module
from knowledge_vault_dashboard import upstream as upstream_module
from knowledge_vault_dashboard.app import PREFIX, create_app
from knowledge_vault_dashboard.sessions import COOKIE, Sessions
from knowledge_vault_dashboard.upstream import BoundaryError, CoreClient

from conftest import CAPABILITIES, CREDENTIAL


async def test_anonymous_probe_burst_is_bounded_and_preserves_browser_budget(settings, monkeypatch):
    calls = 0
    clock = SimpleNamespace(monotonic=lambda: 100.0)
    monkeypatch.setattr(upstream_module, "time", clock)
    monkeypatch.setattr(limits_module, "time", clock)

    async def upstream(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, json=CAPABILITIES if request.url.path.endswith("capabilities") else {"aliases": {}}
        )

    app = create_app(settings, transport=httpx.MockTransport(upstream))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="https://dashboard.test"
        ) as c,
    ):
        statuses = [
            (
                await c.get(
                    "/health/ready",
                    headers={"Host": f"attacker-{n}.invalid", "X-Forwarded-For": str(n)},
                )
            ).status_code
            for n in range(605)
        ]
        assert statuses.count(204) == 605
        assert calls == 1
        cookie = Sessions(settings).issue(CREDENTIAL)
        response = await c.get(PREFIX + "topic-aliases", headers={"Cookie": COOKIE + "=" + cookie})
        assert response.status_code == 200
        assert calls == 3  # Data still performs a fresh capability check and its own read.
        assert (await c.get("/health/live", headers={"Host": "wrong.invalid"})).status_code == 204


async def test_stalled_probe_cannot_occupy_all_user_slots(settings):
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def upstream(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
        return httpx.Response(
            200, json=CAPABILITIES if request.url.path.endswith("capabilities") else {"aliases": {}}
        )

    app = create_app(settings, transport=httpx.MockTransport(upstream))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="https://dashboard.test"
        ) as c,
    ):
        first = asyncio.create_task(c.get("/health/ready", headers={"Host": "wrong.invalid"}))
        await asyncio.wait_for(started.wait(), 1)
        try:
            probes = await asyncio.wait_for(
                asyncio.gather(
                    *[c.get("/health/ready", headers={"Host": "wrong.invalid"}) for _ in range(4)]
                ),
                0.5,
            )
            assert all(probe.status_code == 503 for probe in probes)
            assert calls == 1
            cookie = Sessions(settings).issue(CREDENTIAL)
            read = await asyncio.wait_for(
                c.get(PREFIX + "topic-aliases", headers={"Cookie": COOKIE + "=" + cookie}), 0.5
            )
            assert read.status_code == 200
            assert calls == 3
        finally:
            release.set()
            assert (await first).status_code == 204


async def test_probe_cache_success_failure_expiry_and_recovery(settings, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(upstream_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    calls = 0
    status = [200]

    async def upstream(request):
        nonlocal calls
        calls += 1
        return httpx.Response(status[0], json=CAPABILITIES)

    core = CoreClient(settings, httpx.MockTransport(upstream))
    try:
        await core.ready()
        await core.ready()
        assert calls == 1
        now[0] += 5
        status[0] = 503
        for _ in range(2):
            with pytest.raises(BoundaryError, match="core_unavailable"):
                await core.ready()
        assert calls == 2
        now[0] += 5
        status[0] = 200
        await core.ready()
        assert calls == 3
    finally:
        await core.close()


async def test_probe_deadline_is_short_cached_and_releases_capacity(settings, monkeypatch):
    calls = 0
    original_timeout = asyncio.timeout
    deadlines = []

    def short_timeout(duration):
        deadlines.append(duration)
        return original_timeout(0.01)

    async def upstream(request):
        nonlocal calls
        calls += 1
        await asyncio.Event().wait()

    monkeypatch.setattr(asyncio, "timeout", short_timeout)
    core = CoreClient(settings, httpx.MockTransport(upstream))
    try:
        for _ in range(2):
            with pytest.raises(BoundaryError, match="core_timeout"):
                await core.ready()
        assert deadlines == [2]
        assert calls == 1
        # All four slots were released even though the dependency did not respond.
        async with original_timeout(0.1):
            for _ in range(4):
                await core.slots.acquire()
    finally:
        await core.close()


async def test_cancelled_probe_does_not_cache_success_or_stick_admission(settings):
    started = asyncio.Event()
    calls = 0

    async def upstream(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json=CAPABILITIES)

    core = CoreClient(settings, httpx.MockTransport(upstream))
    probe = asyncio.create_task(core.ready())
    try:
        await asyncio.wait_for(started.wait(), 1)
        probe.cancel()
        with pytest.raises(asyncio.CancelledError):
            await probe
        await core.ready()
        assert calls == 2
    finally:
        await core.close()


async def test_interrupted_probe_admission_is_bounded_without_charging_browser_reads(
    settings, monkeypatch
):
    now = [100.0]
    monkeypatch.setattr(limits_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    core = CoreClient(
        settings,
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=CAPABILITIES if request.url.path.endswith("capabilities") else {"aliases": {}},
            )
        ),
    )
    interrupted = 0
    original = core.capabilities

    async def cancelled_check():
        nonlocal interrupted
        interrupted += 1
        raise asyncio.CancelledError

    monkeypatch.setattr(core, "capabilities", cancelled_check)
    try:
        for _ in range(60):
            with pytest.raises(asyncio.CancelledError):
                await core.ready()
        with pytest.raises(BoundaryError) as denied:
            await core.ready()
        assert denied.value.status == 429
        assert interrupted == 60
        monkeypatch.setattr(core, "capabilities", original)
        assert await core.read("topic-aliases", "topic-aliases", None, {}) == {"aliases": {}}
        now[0] += 60
        await core.ready()
    finally:
        await core.close()
