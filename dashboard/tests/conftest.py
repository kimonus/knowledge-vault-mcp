import hashlib
import hmac
from dataclasses import replace

import httpx
import pytest
from knowledge_vault_dashboard.app import PREFIX, create_app
from knowledge_vault_dashboard.auth.tokens import Scope, TokenRecord
from knowledge_vault_dashboard.config import Settings
from knowledge_vault_dashboard.upstream import CONTRACT

CREDENTIAL = "browser-test-credential-000000000000000000"
SERVICE = "service-test-credential-111111111111111111"
PEPPER = "browser-test-pepper-2222222222222222222222"
SIGNING = "session-test-signing-33333333333333333333"
HOST = "dashboard.test"
HEADERS = {"Origin": "https://" + HOST, "X-Dashboard-Request": "1"}
CAPABILITIES = {
    "protocol_version": "1",
    "operations": CONTRACT["operations"],
    "max_page_size": 100,
    "untrusted_data": True,
}


@pytest.fixture
def settings():
    return Settings(
        enabled=True,
        host=HOST,
        core_url="http://core.default.svc.cluster.local:8000",
        core_host="core-private.test",
        service_token=SERVICE,
        pepper=PEPPER,
        signing_key=SIGNING,
        records=(
            TokenRecord(
                "browser",
                hmac.new(PEPPER.encode(), CREDENTIAL.encode(), hashlib.sha256).digest(),
                frozenset({Scope.READ}),
            ),
        ),
    )


@pytest.fixture
def access_settings(settings):
    return replace(
        settings,
        mode="cloudflareAccess",
        records=(),
        pepper="",
        signing_key="",
        access_issuer="https://team.cloudflareaccess.com",
        access_audience="dashboard",
        access_emails=frozenset({"owner@example.test"}),
    )


@pytest.fixture
def core_transport():
    def handle(request):
        assert request.headers["authorization"] == "Bearer " + SERVICE
        assert request.headers["host"] == "core-private.test"
        assert not {"cookie", "origin", "cf-access-jwt-assertion", "x-forwarded-for"} & set(
            request.headers
        )
        assert request.url.path.startswith("/api/v1/read/")
        if request.url.path.endswith("/capabilities"):
            return httpx.Response(200, json=CAPABILITIES)
        return httpx.Response(200, json={"aliases": {}})

    return httpx.MockTransport(handle)


@pytest.fixture
async def client(settings, core_transport):
    app = create_app(settings, transport=core_transport)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://" + HOST) as client,
    ):
        yield client


@pytest.fixture
async def logged(client):
    response = await client.post(
        PREFIX + "session/login", json={"credential": CREDENTIAL}, headers=HEADERS
    )
    assert response.status_code == 204
    return client
