from uuid import uuid4

import httpx
import pytest
from starlette.routing import Route

from knowledge_vault.api.app import create_app
from knowledge_vault.auth.inspection import InspectionBoundaryMiddleware
from knowledge_vault.auth.tokens import Principal, Scope
from knowledge_vault.container import build_container
from knowledge_vault.domain.responses import InspectionCapabilities


@pytest.fixture
def inspection_container(settings):
    return build_container(settings.model_copy(update={"inspection_principals": "reader"}))


async def test_inspection_token_confined_across_every_existing_http_route(
    inspection_container,
    reader_token,
    writer_token,
    raw_token,
):
    app = create_app(inspection_container)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://private.example"
    ) as client:
        headers = {"Authorization": f"Bearer {reader_token}"}
        for route in app.routes:
            if not isinstance(route, Route) or route.path.startswith("/api/v1/read/"):
                continue
            path = route.path
            for parameter in route.param_convertors:
                path = path.replace(
                    "{" + parameter + "}", "1" if parameter == "part_number" else str(uuid4())
                )
            for method in route.methods:
                response = await client.request(method, path, headers=headers, json={})
                assert response.status_code == 403, (method, path, response.text)
        for method in ["GET", "POST", "DELETE"]:
            response = await client.request(method, "/mcp", headers=headers, json={})
            assert response.status_code == 403
        for token in [writer_token, raw_token, "wrong"]:
            response = await client.get(
                "/api/v1/read/capabilities", headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status_code == 403
        assert (await client.get("/api/v1/read/capabilities")).status_code == 401
        response = await client.get("/api/v1/read/capabilities", headers=headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert InspectionCapabilities.model_validate(response.json()).protocol_version == "1"
        assert (await client.get("/health/live")).status_code == 200
        for path in [
            "/api/v1/read/../statistics",
            "/mcp/",
            "/api/v1/%72ead/nope",
            "/api/v1/read//capabilities",
        ]:
            assert (await client.get(path, headers=headers)).status_code in {403, 404}
        assert (await client.post("/api/v1/read/capabilities", headers=headers)).status_code == 405
        for extra in [
            {"Origin": "https://private.example"},
            {"Cookie": "__Host-kv_dashboard=untrusted"},
            {"Cf-Access-Jwt-Assertion": "unverified"},
        ]:
            assert (
                await client.get("/api/v1/read/capabilities", headers={**headers, **extra})
            ).status_code == 403
        for extra in [{"limit": 101}, {"purpose": "normal"}, {"cursor": "!"}]:
            # Schema failures need no database; cursor integrity is tested with real queries.
            if "cursor" not in extra:
                assert (
                    await client.post("/api/v1/read/assertions/query", headers=headers, json=extra)
                ).status_code == 422
    await inspection_container.database.close()


async def test_boundary_malformed_headers_and_websocket(inspection_container, reader_token):
    called = []
    messages = []

    async def target(scope, receive, send):
        called.append(scope["type"])

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    boundary = InspectionBoundaryMiddleware(
        target, inspection_container.authenticator, frozenset({"reader"})
    )
    auth = (b"authorization", f"Bearer {reader_token}".encode())
    for headers in [
        [auth],
        [auth, (b"host", b"a"), (b"host", b"b")],
        [auth, (b"host", b"\xff")],
        [auth, auth, (b"host", b"a")],
    ]:
        messages.clear()
        await boundary(
            {
                "type": "http",
                "path": "/api/v1/read/capabilities",
                "method": "GET",
                "headers": headers,
            },
            receive,
            send,
        )
        assert messages[0]["status"] == 400
    await boundary({"type": "websocket"}, receive, send)
    assert called == ["websocket"]
    inspection_container.authenticator.authenticate = lambda _: Principal(
        "reader", frozenset({Scope.WRITE})
    )
    messages.clear()
    await boundary(
        {
            "type": "http",
            "path": "/api/v1/read/capabilities",
            "method": "GET",
            "headers": [auth, (b"host", b"a")],
        },
        receive,
        send,
    )
    assert messages[0]["status"] == 403
    await inspection_container.database.close()
