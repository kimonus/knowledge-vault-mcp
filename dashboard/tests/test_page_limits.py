import asyncio

import httpx
import pytest
from knowledge_vault_dashboard.app import PREFIX
from knowledge_vault_dashboard.upstream import BoundaryError, CoreClient

from conftest import CAPABILITIES


async def test_session_exposes_public_page_limit(logged):
    response = await logged.get(PREFIX + "session")
    assert response.status_code == 200
    assert response.json()["max_page_size"] == CAPABILITIES["max_page_size"]


@pytest.mark.parametrize("maximum", [1, 10, 100])
async def test_small_cap_does_not_block_unpaged_reads_or_bounded_history(settings, maximum):
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path.endswith("capabilities"):
            return httpx.Response(200, json={**CAPABILITIES, "max_page_size": maximum})
        if request.url.path.endswith("topic-aliases"):
            return httpx.Response(200, json={"aliases": {}})
        return httpx.Response(404)

    core = CoreClient(settings, httpx.MockTransport(handle))
    try:
        assert await core.page_limit() == maximum
        assert (await core.read("topic-aliases", "topic-aliases", None, {}))["aliases"] == {}
        identity = {
            "subject": "homelab",
            "property": "backup",
            "scope": "personal",
            "state_type": "configuration",
        }
        with pytest.raises(BoundaryError, match="core_request_refused"):
            await core.read(
                "state/resolve", "state/resolve", {"identity": identity, "history_limit": 1}, {}
            )
        assert paths[-1].endswith("state/resolve")
        if maximum < 100:
            with pytest.raises(BoundaryError, match="invalid_input"):
                await core.read(
                    "state/resolve",
                    "state/resolve",
                    {"identity": identity, "history_limit": maximum + 1},
                    {},
                )
            assert paths[-1].endswith("capabilities")
    finally:
        await core.close()


async def test_session_capability_queue_is_bounded(settings, monkeypatch):
    core = CoreClient(settings)
    core.slots = asyncio.Semaphore(0)
    timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, "timeout", lambda _: timeout(0.01))
    try:
        with pytest.raises(BoundaryError, match="core_timeout"):
            await core.page_limit()
    finally:
        await core.close()
