from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Depends
from pydantic import ValidationError

from knowledge_vault.api.app import create_app
from knowledge_vault.api.auth import require_scope
from knowledge_vault.api.middleware import StandaloneHostMiddleware
from knowledge_vault.auth.hosts import strict_host
from knowledge_vault.auth.tokens import Scope
from knowledge_vault.config import Settings
from knowledge_vault.container import build_container
from knowledge_vault.persistence.schema_guard import SchemaGuard


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("LOCALHOST.:8443", "localhost"),
        ("vault.example:443", "vault.example"),
        ("[0:0:0:0:0:0:0:1]:443", "[::1]"),
        ("127.0.0.1", "127.0.0.1"),
    ],
)
def test_strict_host_normalizes_valid_authorities(value: str, expected: str) -> None:
    assert strict_host(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "*",
        "localhost,evil",
        "localhost:foo",
        "localhost:",
        "localhost:0",
        "localhost:65536",
        "localhost:1:2",
        "localhost..",
        "localhost\n",
        " localhost",
        "local_host",
        "user@localhost",
        "[::1]junk",
        "[::1",
        "::1",
        "[fe80::1%eth0]",
    ],
)
def test_strict_host_rejects_ambiguous_authorities(value: str) -> None:
    with pytest.raises(ValueError):
        strict_host(value)


@pytest.mark.parametrize("allowlist", ["", " ", "*", "localhost,", "localhost, *.example"])
def test_standalone_configuration_fails_closed(allowlist: str) -> None:
    with pytest.raises(ValidationError):
        Settings(environment="test", standalone_mode=True, standalone_allowed_hosts=allowlist)


async def test_host_guard_rejects_missing_duplicate_and_invalid_headers() -> None:
    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del scope, receive
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def invoke(headers: list[tuple[bytes, bytes]], path: str = "/mcp") -> int:
        sent: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b""}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await StandaloneHostMiddleware(app, frozenset({"localhost", "[::1]"}))(
            {"type": "http", "path": path, "headers": headers}, receive, send
        )
        return sent[0]["status"]

    assert await invoke([(b"host", b"LOCALHOST.:8443")]) == 204
    assert await invoke([(b"host", b"[::1]:443")]) == 204
    assert await invoke([]) == 400
    assert await invoke([(b"host", b"localhost"), (b"Host", b"localhost")]) == 400
    assert await invoke([(b"host", b"localhost:abc")]) == 400
    assert await invoke([(b"host", b"\xff")]) == 400
    assert await invoke([(b"host", b"attacker.example")]) == 400
    assert await invoke([], "/health/live") == 204


async def test_host_guard_wraps_cors_and_retains_authentication(monkeypatch) -> None:
    monkeypatch.setattr(SchemaGuard, "ready", AsyncMock(return_value=True))
    settings = Settings(
        environment="test",
        embeddings_enabled=False,
        standalone_mode=True,
        standalone_allowed_hosts="localhost",
        cors_origins="https://tools.example",
    )
    app = create_app(build_container(settings))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        headers = {"origin": "https://tools.example", "access-control-request-method": "POST"}
        assert (
            await client.options("/mcp", headers={**headers, "host": "evil.example"})
        ).status_code == 400
        assert (await client.options("/mcp", headers=headers)).status_code == 200
        assert (await client.get("/api/v1/statistics")).status_code in {401, 403}
        assert (
            await client.get("/health/live", headers={"host": "other.example"})
        ).status_code == 200


async def test_allowed_host_preserves_bearer_scopes_and_origin(
    settings: Settings, reader_token: str, monkeypatch
) -> None:
    monkeypatch.setattr(SchemaGuard, "ready", AsyncMock(return_value=True))
    configured = Settings(
        **(
            settings.model_dump()
            | {
                "standalone_mode": True,
                "standalone_allowed_hosts": "localhost",
                "cors_origins": "https://tools.example",
            }
        )
    )
    app = create_app(build_container(configured))

    @app.get("/read-probe", dependencies=[Depends(require_scope(Scope.READ))])
    async def read_probe() -> dict[str, bool]:
        return {"ok": True}

    app.router.routes.insert(0, app.router.routes.pop())

    @app.get("/admin-probe", dependencies=[Depends(require_scope(Scope.ADMIN))])
    async def admin_probe() -> dict[str, bool]:
        return {"ok": True}

    app.router.routes.insert(0, app.router.routes.pop())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        bearer = {"authorization": f"Bearer {reader_token}"}
        assert (await client.get("/read-probe", headers=bearer)).status_code == 200
        assert (await client.get("/admin-probe", headers=bearer)).status_code == 403
        assert (await client.get("/read-probe")).status_code == 401
        assert (
            await client.get("/read-probe", headers={**bearer, "host": "evil.example"})
        ).status_code == 400
        assert (
            await client.get("/mcp", headers={**bearer, "origin": "https://evil.example"})
        ).status_code == 403


async def test_standalone_guard_composes_with_cloudflare_access(
    settings: Settings, reader_token: str, monkeypatch
) -> None:
    monkeypatch.setattr(SchemaGuard, "ready", AsyncMock(return_value=True))
    configured = Settings(
        **(
            settings.model_dump()
            | {
                "standalone_mode": True,
                "standalone_allowed_hosts": "public.example,private.example",
                "cloudflare_access_enabled": True,
                "cloudflare_access_issuer_url": "https://example.cloudflareaccess.com",
                "cloudflare_access_audience": "audience",
                "cloudflare_access_allowed_emails": "owner@example.com",
                "cloudflare_access_public_hosts": "public.example",
                "cloudflare_access_private_hosts": "private.example",
            }
        )
    )
    app = create_app(build_container(configured))

    @app.get("/read-probe", dependencies=[Depends(require_scope(Scope.READ))])
    async def read_probe() -> dict[str, bool]:
        return {"ok": True}

    app.router.routes.insert(0, app.router.routes.pop())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://private.example"
    ) as client:
        bearer = {"authorization": f"Bearer {reader_token}"}
        assert (await client.get("/read-probe", headers=bearer)).status_code == 200
        assert (
            await client.get("/read-probe", headers={**bearer, "host": "public.example"})
        ).status_code == 401
        assert (
            await client.get(
                "/read-probe",
                headers={**bearer, "host": "evil.example", "cf-access-jwt-assertion": "invalid"},
            )
        ).status_code == 400
