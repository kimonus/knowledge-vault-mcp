import json
import logging
import sys

import httpx
import jwt
import pytest
from knowledge_vault_dashboard.app import (
    MAX_REQUEST,
    PREFIX,
    SECURITY_HEADERS,
    BrowserBoundary,
    RateGuard,
    create_app,
)
from knowledge_vault_dashboard.sessions import COOKIE, Sessions
from knowledge_vault_dashboard.upstream import BoundaryError, CoreClient

from conftest import CAPABILITIES, CREDENTIAL, HEADERS, HOST, SERVICE, SIGNING


async def test_disabled_is_inaccessible():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://wrong") as c:
        assert (await c.get("/health/live")).status_code == 204
        assert (await c.get("/health/ready")).status_code == 503
        assert (await c.get(PREFIX + "session")).status_code == 404


async def test_session_cookie_and_logout(logged):
    response = await logged.get(PREFIX + "session")
    assert response.status_code == 200
    data = response.json()
    assert data["principal_id"] == "browser"
    cookie = logged.cookies.get(COOKIE)
    response = await logged.post(PREFIX + "session/logout", headers=HEADERS, json={})
    assert response.status_code == 403
    response = await logged.post(
        PREFIX + "session/logout", json={}, headers={**HEADERS, "X-CSRF-Token": data["csrf_token"]}
    )
    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert (await logged.get(PREFIX + "session")).status_code == 401
    # Documented local logout limitation: copied cookies remain valid until revoked/expired.
    assert (
        await logged.get(PREFIX + "session", headers={"Cookie": COOKIE + "=" + cookie})
    ).status_code == 200


async def test_cookie_attributes_and_safe_login(client, caplog):
    caplog.set_level(logging.INFO)
    response = await client.post(
        PREFIX + "session/login", json={"credential": CREDENTIAL}, headers=HEADERS
    )
    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    for attribute in ["HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=1800"]:
        assert attribute in cookie
    assert "Domain=" not in cookie
    claims = jwt.decode(client.cookies.get(COOKIE), options={"verify_signature": False})
    assert set(claims) == {"sub", "aud", "iat", "exp", "nonce", "ref"}
    assert claims["exp"] - claims["iat"] == 1800
    for secret in [CREDENTIAL, SERVICE, SIGNING]:
        assert secret not in caplog.text + response.text + json.dumps(claims)


@pytest.mark.parametrize(
    "body,status",
    [
        ({}, 422),
        ({"credential": "wrong"}, 401),
        ({"credential": SERVICE}, 401),
        ({"credential": 1}, 422),
        ({"credential": CREDENTIAL, "scopes": ["knowledge:admin"]}, 422),
        ([], 422),
    ],
)
async def test_login_invalid(client, body, status):
    assert (
        await client.post(PREFIX + "session/login", json=body, headers=HEADERS)
    ).status_code == status


@pytest.mark.parametrize(
    "headers,status",
    [
        ({"host": "attacker.test"}, 403),
        ({"host": "dashboard.test@attacker.test"}, 403),
        ({"host": ""}, 403),
        ({"host": "dashboard.test,attacker.test"}, 403),
        ({"origin": "https://sibling.dashboard.test"}, 403),
        ({"origin": "null"}, 403),
        ({"origin": "http://dashboard.test"}, 403),
        ({"origin": "https://dashboard.test:443"}, 403),
        ({"sec-fetch-site": "cross-site"}, 403),
        ({"sec-fetch-site": "same-site"}, 403),
        ({"authorization": "Bearer " + SERVICE}, 403),
    ],
)
async def test_host_origin_authority_attacks(logged, headers, status):
    response = await logged.get(PREFIX + "session", headers=headers)
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("name", ["host", "origin", "cookie", "authorization", "sec-fetch-site"])
async def test_repeated_headers(logged, name):
    response = await logged.get(PREFIX + "session", headers=[(name, HOST), (name, HOST)])
    assert response.status_code == 400


async def test_forwarding_never_changes_identity(logged):
    assert (
        await logged.get(
            PREFIX + "topic-aliases",
            headers={
                "x-forwarded-host": "attacker",
                "x-forwarded-for": "1.2.3.4",
                "x-forwarded-proto": "http",
                "cf-access-jwt-assertion": "spoof",
            },
        )
    ).status_code == 200
    assert (
        await logged.get(PREFIX + "session", headers={"host": "DASHBOARD.TEST.:443"})
    ).status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/mcp",
        "/api/v1/read/capabilities",
        "/api/v1/search",
        "/api/v1/flushes",
        "/api/v1/forget",
        "/docs",
        "/openapi.json",
        "/dashboard/explorer",
        PREFIX + "proxy",
        PREFIX + "https://attacker.test",
        PREFIX + "ingestion",
        PREFIX + "assertions/%2e%2e/topic-aliases",
        PREFIX + "%74opic-aliases",
        PREFIX + "//topic-aliases",
        PREFIX + "session/",
        "/dashboard/missing.js",
    ],
)
async def test_routes_confined(logged, path):
    response = await logged.get(path)
    assert response.status_code == 404
    assert response.headers.get("content-type", "").startswith(
        "application/json"
    ) or response.headers.get("content-type", "").startswith("application/problem+json")


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "https://dashboard.test"},
        {**HEADERS, "Origin": "https://other.test"},
        {**HEADERS, "X-Dashboard-Request": "0"},
        {**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
    ],
)
async def test_post_csrf(client, headers):
    assert (
        await client.post(
            PREFIX + "session/login", json={"credential": CREDENTIAL}, headers=headers
        )
    ).status_code == 403


async def test_input_bounds_and_methods(logged):
    for method in ["PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"]:
        assert (await logged.request(method, PREFIX + "session")).status_code == 405
    assert (
        await logged.post(
            PREFIX + "assertions/query",
            content=b"x" * (MAX_REQUEST + 1),
            headers={**HEADERS, "content-type": "application/json"},
        )
    ).status_code == 413
    for body in [
        b"{",
        b"[]",
        b'{"purpose":"usage"}',
        b'{"limit":101}',
        b'{"filters":{"context_id":"bad"}}',
    ]:
        assert (
            await logged.post(
                PREFIX + "assertions/query",
                content=body,
                headers={**HEADERS, "content-type": "application/json"},
            )
        ).status_code == 422
    for path in [
        "session?token=secret",
        "topic-aliases?url=https://attacker",
        "assertions/not-uuid",
        "artifacts/00000000-0000-0000-0000-000000000001?limit=8001",
        "assertions/00000000-0000-0000-0000-000000000001/sources?limit=0",
        "assertions/00000000-0000-0000-0000-000000000001/sources?cursor=",
        "topic-aliases?limit=1&limit=2",
    ]:
        assert (await logged.get(PREFIX + path)).status_code == 422
    assert (await logged.request("GET", PREFIX + "session", content=b"body")).status_code == 422
    assert (
        await logged.post(
            PREFIX + "session/logout", json={}, headers={**HEADERS, "X-CSRF-Token": "bad"}
        )
    ).status_code == 403


async def test_headers_on_data_and_errors(logged):
    for path in ["topic-aliases", "missing", "session"]:
        response = await logged.get(PREFIX + path)
        for name, value in SECURITY_HEADERS.items():
            assert response.headers[name] == value
        assert "access-control-allow-origin" not in response.headers
        assert "'unsafe-inline'" not in response.headers["content-security-policy"]


def test_rate_guard_bounded_and_global():
    guard = RateGuard()
    for i in range(1100):
        guard.check(str(i), 1)
    assert len(guard.buckets) == 1024
    with pytest.raises(BoundaryError):
        guard.check("1099", 1)


async def test_login_rate_limit_does_not_trust_forwarding(client):
    for i in range(10):
        response = await client.post(
            PREFIX + "session/login",
            json={"credential": "wrong"},
            headers={**HEADERS, "X-Forwarded-For": str(i)},
        )
        assert response.status_code == 401
    response = await client.post(
        PREFIX + "session/login", json={"credential": CREDENTIAL}, headers=HEADERS
    )
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


@pytest.mark.parametrize(
    "status,expected",
    [
        (301, 502),
        (302, 502),
        (307, 502),
        (401, 503),
        (403, 503),
        (500, 503),
        (404, 404),
        (422, 422),
        (429, 429),
    ],
)
async def test_safe_upstream_errors(settings, status, expected):
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            status,
            json={"secret": SERVICE},
            headers={
                "Retry-After": "30",
                "Location": "https://attacker.test",
                "Set-Cookie": "stolen",
            },
        )
    )
    core = CoreClient(settings, transport)
    try:
        with pytest.raises(BoundaryError) as result:
            await core.ready()
        assert result.value.status == expected
        assert SERVICE not in str(result.value)
        assert result.value.retry_after == ("30" if status == 429 else None)
    finally:
        await core.close()


@pytest.mark.parametrize(
    "response,expected",
    [
        (
            httpx.Response(200, content="not JSON", headers={"content-type": "application/json"}),
            502,
        ),
        (httpx.Response(200, json={}), 502),
        (httpx.Response(200, json={**CAPABILITIES, "protocol_version": "2"}), 502),
        (httpx.Response(200, json={**CAPABILITIES, "operations": []}), 503),
        (httpx.Response(200, json={**CAPABILITIES, "max_page_size": 0}), 503),
        (
            httpx.Response(
                200, content=b"x" * 2_000_001, headers={"content-type": "application/json"}
            ),
            502,
        ),
        (httpx.Response(200, text="<html>private</html>"), 502),
    ],
)
async def test_invalid_upstream(settings, response, expected):
    core = CoreClient(settings, httpx.MockTransport(lambda r: response))
    try:
        with pytest.raises(BoundaryError) as result:
            await core.ready()
        assert result.value.status == expected
    finally:
        await core.close()


@pytest.mark.parametrize(
    "error,expected", [(httpx.ReadTimeout("private"), 504), (httpx.ConnectError("private"), 503)]
)
async def test_upstream_transport_failure(settings, error, expected):
    def fail(request):
        raise error

    core = CoreClient(settings, httpx.MockTransport(fail))
    with pytest.raises(BoundaryError) as result:
        await core.ready()
    assert result.value.status == expected
    assert "private" not in str(result.value)
    await core.close()


async def test_adapter_rejects_arbitrary_path(settings, core_transport):
    core = CoreClient(settings, core_transport)
    for operation, path in [
        ("proxy", "mcp"),
        ("topic-aliases", "https://attacker.test"),
        ("capabilities", "capabilities"),
        ("assertions/{id}", "assertions/" + "-" * 36),
    ]:
        with pytest.raises(BoundaryError):
            await core.read(operation, path, None, {})
    await core.close()


def test_runtime_has_no_core_database_or_model_modules():
    assert not any(
        name.split(".")[0]
        in {"knowledge_vault", "sqlalchemy", "psycopg", "torch", "sentence_transformers"}
        for name in sys.modules
    )


async def test_websocket_boundary_passes_to_app(settings):
    observed = []

    async def downstream(scope, receive, send):
        observed.append(scope["type"])

    boundary = BrowserBoundary(downstream, settings, Sessions(settings), None)
    await boundary({"type": "websocket"}, None, None)
    assert observed == ["websocket"]
