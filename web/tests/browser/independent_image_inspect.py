import asyncio
import importlib.util
import secrets
import shutil
from pathlib import Path

import httpx
from knowledge_vault_dashboard.app import create_app
from knowledge_vault_dashboard.assets import Assets
from knowledge_vault_dashboard.auth.tokens import Scope, TokenRecord, hash_token
from knowledge_vault_dashboard.config import Settings
from knowledge_vault_dashboard.upstream import CONTRACT


async def inspect():
    assert __import__("os").getuid() == 10001
    for module in ("knowledge_vault", "sqlalchemy", "psycopg", "torch", "sentence_transformers"):
        assert importlib.util.find_spec(module) is None, module
    assert shutil.which("node") is None
    assert shutil.which("npm") is None
    package = Path(importlib.util.find_spec("knowledge_vault_dashboard").origin).parent
    assert not list(package.rglob("*.map"))
    assets = Assets()
    assert assets.index and len(assets.files) == 2
    all_bytes = assets.index.encode() + b"".join(content for content, _ in assets.files.values())
    for marker in [
        b"browser-fixture-credential",
        b"Nightly backup configuration retained",
        b"__vault_xss",
        b"sourceMappingURL",
    ]:
        assert marker not in all_bytes, marker
    assert not list(package.rglob("fixtures.json"))
    pepper = secrets.token_urlsafe(36)
    credential = secrets.token_urlsafe(36)
    settings = Settings(
        enabled=True,
        host="review.test",
        core_url="http://core.default.svc.cluster.local",
        core_host="core-private.test",
        service_token=secrets.token_urlsafe(36),
        pepper=pepper,
        signing_key=secrets.token_urlsafe(36),
        records=(TokenRecord("review", hash_token(credential, pepper), frozenset({Scope.READ})),),
    )
    healthy = True

    def core(request):
        if healthy and request.url.path.endswith("/capabilities"):
            return httpx.Response(
                200,
                json={
                    "protocol_version": "1",
                    "operations": CONTRACT["operations"],
                    "max_page_size": 10,
                    "untrusted_data": True,
                },
            )
        return httpx.Response(503)

    app = create_app(settings, transport=httpx.MockTransport(core))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="https://review.test"
    ) as client:
        for path in ["/dashboard/", "/dashboard/portal/topics/cHJpdmF0ZS10b3BpYw", *assets.files]:
            response = await client.get(path)
            assert response.status_code == 200, (path, response.status_code)
            for header, value in [
                ("cache-control", "no-store"),
                ("referrer-policy", "no-referrer"),
                ("x-content-type-options", "nosniff"),
            ]:
                assert response.headers[header] == value
            assert "default-src 'none'" in response.headers["content-security-policy"]
        for path in [
            "/dashboard/unknown",
            "/dashboard/assets/secret.js.map",
            "/dashboard/assets/.env",
            "/mcp",
            "/api/v1/read/capabilities",
        ]:
            assert (await client.get(path)).status_code == 404, path
        assert (await client.get("/dashboard/api/v1/topic-aliases")).status_code == 401
        login = await client.post(
            "/dashboard/api/v1/session/login",
            json={"credential": credential},
            headers={"Origin": "https://review.test", "X-Dashboard-Request": "1"},
        )
        assert login.status_code == 204
        session = await client.get("/dashboard/api/v1/session")
        assert session.status_code == 200
        assert session.json()["max_page_size"] == 10
        healthy = False
        assert (await client.get("/dashboard/api/v1/session")).status_code == 503
    print(
        "Independent image inspection PASS: non-root/read-only, no Node/core/model/DB modules, "
        "two public JS/CSS assets, no maps/fixture corpus, exact routes and anonymous denials, "
        "headers and private cookie login."
    )


asyncio.run(inspect())
