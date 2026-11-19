import asyncio
import gzip
import time

import httpx
import pytest
from knowledge_vault_dashboard.app import MAX_REQUEST, PREFIX, BrowserBoundary
from knowledge_vault_dashboard.sessions import COOKIE, Sessions
from knowledge_vault_dashboard.upstream import BoundaryError, CoreClient

from conftest import CAPABILITIES, CREDENTIAL, HEADERS, HOST, SERVICE


@pytest.mark.parametrize(
    "content",
    [
        b'{"credential":"one","credential":"two"}',
        b'{"credential":NaN}',
        b'{"credential":"\\ud800"}',
        b'{"credential":"abc\\n"}',
    ],
)
async def test_ambiguous_and_non_json_login(client, content):
    response = await client.post(
        PREFIX + "session/login",
        content=content,
        headers={**HEADERS, "content-type": "application/json"},
    )
    assert response.status_code == 422


async def test_untrusted_json_remains_json_and_service_echo_refused(settings):
    marker = '<script src="https://evil.test/steal.js"></script>'

    async def run(payload):
        def handle(request):
            return httpx.Response(
                200, json=CAPABILITIES if request.url.path.endswith("capabilities") else payload
            )

        core = CoreClient(settings, httpx.MockTransport(handle))
        try:
            return await core.read("topic-aliases", "topic-aliases", None, {})
        finally:
            await core.close()

    result = await run({"aliases": {marker: marker}, "untrusted_data": True})
    assert result["aliases"][marker] == marker
    with pytest.raises(BoundaryError, match="invalid_response"):
        await run({"aliases": {"echo": SERVICE}})


async def test_compressed_response_refused(settings):
    core = CoreClient(
        settings,
        httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                content=gzip.compress(b"{}"),
                headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
            )
        ),
    )
    with pytest.raises(BoundaryError, match="invalid_response"):
        await core.ready()
    await core.close()


async def test_chunked_body_overflow_disconnect_and_invalid_length(settings):
    sessions = Sessions(settings)
    cookie = sessions.issue(CREDENTIAL)
    boundary = BrowserBoundary(None, settings, sessions, None)
    scope = {
        "type": "http",
        "path": PREFIX + "assertions/query",
        "method": "POST",
        "headers": [
            (b"host", HOST.encode()),
            (b"origin", ("https://" + HOST).encode()),
            (b"x-dashboard-request", b"1"),
            (b"content-type", b"application/json"),
            (b"cookie", (COOKIE + "=" + cookie).encode()),
        ],
    }
    chunks = iter(
        [
            {"type": "http.request", "body": b"x" * 64000, "more_body": True},
            {"type": "http.request", "body": b"x" * (MAX_REQUEST - 64000 + 1), "more_body": False},
        ]
    )

    async def receive():
        return next(chunks)

    with pytest.raises(BoundaryError, match="request_too_large"):
        await boundary.authorize(scope, receive)

    async def disconnected():
        return {"type": "http.disconnect"}

    with pytest.raises(BoundaryError, match="request_disconnected"):
        await boundary.authorize(scope, disconnected)
    scope["headers"].append((b"content-length", b"not-a-number"))
    with pytest.raises(BoundaryError, match="request_too_large"):
        await boundary.authorize(scope, disconnected)


async def test_absolute_budget_includes_slot_queue(settings, monkeypatch):
    core = CoreClient(
        settings, httpx.MockTransport(lambda r: httpx.Response(200, json=CAPABILITIES))
    )
    original_timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, "timeout", lambda duration: original_timeout(0.01))
    core.slots = asyncio.Semaphore(0)
    for operation in [core.ready(), core.read("topic-aliases", "topic-aliases", None, {})]:
        started = time.monotonic()
        with pytest.raises(BoundaryError, match="core_timeout"):
            await operation
        assert time.monotonic() - started < 1
    await core.close()


async def test_concurrent_calls_bound_to_four_and_no_retry(settings):
    in_flight = 0
    maximum = 0
    calls = 0
    released = asyncio.Event()

    async def handle(request):
        nonlocal in_flight, maximum, calls
        calls += 1
        in_flight += 1
        maximum = max(maximum, in_flight)
        if maximum == 4:
            released.set()
        await released.wait()
        await asyncio.sleep(0)
        in_flight -= 1
        return httpx.Response(
            200, json=CAPABILITIES if request.url.path.endswith("capabilities") else {"aliases": {}}
        )

    core = CoreClient(settings, httpx.MockTransport(handle))
    await asyncio.wait_for(
        asyncio.gather(*[core.read("topic-aliases", "topic-aliases", None, {}) for _ in range(8)]),
        timeout=2,
    )
    assert maximum == 4
    assert calls == 16  # One capability check and one data call each; no retry/fan-out.
    await core.close()


async def test_request_upload_deadline(settings, monkeypatch):
    boundary = BrowserBoundary(None, settings, Sessions(settings), None)
    scope = {
        "type": "http",
        "path": PREFIX + "session/login",
        "method": "POST",
        "headers": [
            (b"host", HOST.encode()),
            (b"origin", ("https://" + HOST).encode()),
            (b"x-dashboard-request", b"1"),
            (b"content-type", b"application/json"),
        ],
    }
    original_timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, "timeout", lambda duration: original_timeout(0.01))

    async def receive():
        await asyncio.Event().wait()

    with pytest.raises(BoundaryError, match="request_timeout"):
        await boundary.authorize(scope, receive)
