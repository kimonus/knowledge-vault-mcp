import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from knowledge_vault_dashboard.app import PREFIX, create_app
from knowledge_vault_dashboard.auth.cloudflare import (
    CloudflareAccessAuthenticator,
    CloudflareAccessError,
    CloudflareJWKSClient,
    bind_cloudflare_principal,
    current_cloudflare_principal,
)
from knowledge_vault_dashboard.auth.tokens import Principal, Scope

from conftest import HEADERS, HOST


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(key, **overrides):
    now = int(time.time())
    claims = {
        "iss": "https://team.cloudflareaccess.com",
        "aud": "dashboard",
        "sub": "owner",
        "email": "owner@example.test",
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test"})


def verifier(key, **kwargs):
    class Keys:
        async def get_signing_key(self, assertion):
            return key.public_key()

    return CloudflareAccessAuthenticator(
        issuer_url="https://team.cloudflareaccess.com",
        audience="dashboard",
        allowed_emails=frozenset({"owner@example.test"}),
        scopes=frozenset({Scope.READ}),
        signing_keys=Keys(),
        **kwargs,
    )


async def test_access_each_request_no_private_fallback(access_settings, key, core_transport):
    app = create_app(access_settings, transport=core_transport, access=verifier(key))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://" + HOST) as client,
    ):
        assertion = token(key)
        headers = {"Cf-Access-Jwt-Assertion": assertion}
        response = await client.get(PREFIX + "session", headers=headers)
        assert response.status_code == 200
        assert response.json()["auth_mode"] == "cloudflareAccess"
        assert "set-cookie" not in response.headers
        assert (await client.get(PREFIX + "topic-aliases", headers=headers)).status_code == 200
        assert (await client.get(PREFIX + "session")).status_code == 401
        assert (
            await client.get(PREFIX + "session", headers={"cookie": "__Host-kv_dashboard=bad"})
        ).status_code == 401
        assert (
            await client.post(PREFIX + "session/login", headers=HEADERS, json={})
        ).status_code == 404
        assert (
            await client.post(
                PREFIX + "session/logout",
                headers={**HEADERS, **headers, "X-CSRF-Token": response.json()["csrf_token"]},
                json={},
            )
        ).status_code == 204
        assert (await client.get(PREFIX + "session", headers=headers)).status_code == 200
        for value in ["spoof", token(key, aud="core"), token(key, email="other@example.test")]:
            assert (
                await client.get(PREFIX + "session", headers={"Cf-Access-Jwt-Assertion": value})
            ).status_code == 401
    # Constructing the default verifier needs no core runtime or network call.
    create_app(access_settings, transport=core_transport)


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "wrong"},
        {"iss": "https://wrong"},
        {"exp": 1},
        {"iat": int(time.time()) + 1000},
        {"email": "wrong@example.test"},
        {"email": ""},
        {"email": 5},
    ],
)
async def test_access_rejects_invalid_claims(key, overrides):
    with pytest.raises(CloudflareAccessError):
        await verifier(key).authenticate(token(key, **overrides))


async def test_access_wrong_signature_algorithm_and_context(key):
    wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for assertion in [
        token(wrong),
        jwt.encode({"exp": int(time.time()) + 100}, "x" * 32, algorithm="HS256"),
        "spoof",
    ]:
        with pytest.raises(CloudflareAccessError):
            await verifier(key).authenticate(assertion)
    principal = Principal("access", frozenset({Scope.READ}))
    assert current_cloudflare_principal() is None
    with bind_cloudflare_principal(principal):
        assert current_cloudflare_principal() == principal
    assert current_cloudflare_principal() is None


async def test_deeply_nested_access_header_is_unauthorized():
    from jwt.utils import base64url_encode

    header = base64url_encode(b'{"kid":' + b"[" * 1100 + b"0" + b"]" * 1100 + b"}").decode()
    verifier = CloudflareAccessAuthenticator(
        issuer_url="https://team.cloudflareaccess.com",
        audience="dashboard",
        allowed_emails=frozenset({"owner@example.test"}),
        scopes=frozenset({Scope.READ}),
    )
    with pytest.raises(CloudflareAccessError):
        await verifier.authenticate(header + ".e30.signature")


async def test_jwks_cache_refresh_and_outage(key):
    import json

    calls = []
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk["kid"] = "test"

    def handle(request):
        calls.append(request.url)
        return httpx.Response(200, json={"keys": [jwk]})

    client = CloudflareJWKSClient(
        "https://team.cloudflareaccess.com/cdn-cgi/access/certs",
        transport=httpx.MockTransport(handle),
    )
    assertion = token(key)
    assert await client.get_signing_key(assertion)
    assert await client.get_signing_key(assertion)
    assert len(calls) == 1
    for value in [
        jwt.encode({}, key, algorithm="RS256", headers={"kid": "unknown"}),
        jwt.encode({}, key, algorithm="RS256"),
    ]:
        with pytest.raises(CloudflareAccessError):
            await client.get_signing_key(value)
    for response in [
        httpx.Response(503),
        httpx.Response(302, headers={"Location": "https://evil"}),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"keys": []}),
        httpx.Response(200, text="bad"),
    ]:
        client = CloudflareJWKSClient(
            "https://team.cloudflareaccess.com/certs",
            transport=httpx.MockTransport(lambda r, response=response: response),
        )
        with pytest.raises(CloudflareAccessError):
            await client.get_signing_key(assertion)
