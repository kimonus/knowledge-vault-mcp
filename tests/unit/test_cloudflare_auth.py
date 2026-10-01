import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import MockTransport, Request, Response
from pydantic import ValidationError

from knowledge_vault.api.middleware import CloudflareAccessMiddleware
from knowledge_vault.auth.cloudflare import (
    CloudflareAccessAuthenticator,
    CloudflareAccessError,
    CloudflareJWKSClient,
    bind_cloudflare_principal,
    current_cloudflare_principal,
)
from knowledge_vault.auth.mcp import MCPTokenVerifier
from knowledge_vault.auth.tokens import Principal, Scope, TokenAuthenticator
from knowledge_vault.config import Settings


class StaticSigningKeys:
    def __init__(self, key: object) -> None:
        self._key = key

    async def get_signing_key(self, token: str) -> Any:
        del token
        return self._key


def make_authenticator() -> tuple[CloudflareAccessAuthenticator, object]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    authenticator = CloudflareAccessAuthenticator(
        issuer_url="https://example.cloudflareaccess.com",
        audience="access-audience",
        allowed_emails=frozenset({"owner@example.com"}),
        scopes=frozenset({Scope.READ, Scope.WRITE}),
        signing_keys=StaticSigningKeys(private_key.public_key()),
    )
    return authenticator, private_key


def assertion(private_key: object, **overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": "https://example.cloudflareaccess.com",
        "aud": ["access-audience"],
        "sub": "identity-id",
        "email": "Owner@Example.com",
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test"})


async def test_cloudflare_assertion_maps_identity_to_bounded_scopes() -> None:
    authenticator, private_key = make_authenticator()
    principal = await authenticator.authenticate(assertion(private_key))
    assert principal.id == "cloudflare:owner@example.com"
    assert principal.scopes == frozenset({Scope.READ, Scope.WRITE})
    assert not principal.permits(Scope.ADMIN)


async def test_cloudflare_assertion_rejects_wrong_identity_and_audience() -> None:
    authenticator, private_key = make_authenticator()
    with pytest.raises(CloudflareAccessError):
        await authenticator.authenticate(assertion(private_key, email="other@example.com"))
    with pytest.raises(CloudflareAccessError):
        await authenticator.authenticate(assertion(private_key, aud="wrong-audience"))


async def test_jwks_client_fetches_and_caches_signing_key() -> None:
    _, private_key = make_authenticator()
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk["kid"] = "test"
    requests = 0

    def handler(request: Request) -> Response:
        nonlocal requests
        requests += 1
        assert request.url.path == "/cdn-cgi/access/certs"
        return Response(200, json={"keys": [jwk]})

    client = CloudflareJWKSClient(
        "https://example.cloudflareaccess.com/cdn-cgi/access/certs",
        transport=MockTransport(handler),
    )
    token = assertion(private_key)
    assert await client.get_signing_key(token) is not None
    assert await client.get_signing_key(token) is not None
    assert requests == 1


async def test_jwks_client_rejects_missing_or_unknown_key_id() -> None:
    _, private_key = make_authenticator()
    client = CloudflareJWKSClient(
        "https://example.cloudflareaccess.com/cdn-cgi/access/certs",
        transport=MockTransport(lambda request: Response(200, json={"keys": []})),
    )
    token_without_key_id = jwt.encode({"sub": "identity-id"}, private_key, algorithm="RS256")
    with pytest.raises(CloudflareAccessError, match="no key ID"):
        await client.get_signing_key(token_without_key_id)
    with pytest.raises(CloudflareAccessError, match="signing keys are unavailable"):
        await client.get_signing_key(assertion(private_key))


async def test_middleware_binds_identity_and_adds_non_secret_bearer_marker() -> None:
    authenticator, private_key = make_authenticator()
    observed: dict[str, Any] = {}

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del receive
        observed["principal"] = current_cloudflare_principal()
        observed["headers"] = dict(scope["headers"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = CloudflareAccessMiddleware(app, authenticator)
    await middleware(
        {
            "type": "http",
            "headers": [(b"cf-access-jwt-assertion", assertion(private_key).encode())],
        },  # type: ignore[arg-type]
        receive,
        send,
    )
    assert sent[0]["status"] == 204
    assert observed["principal"].id == "cloudflare:owner@example.com"
    assert observed["headers"][b"authorization"] == b"Bearer cloudflare-access-verified"
    assert current_cloudflare_principal() is None


async def test_middleware_requires_assertion_on_public_host_only() -> None:
    authenticator, _ = make_authenticator()
    delegated: list[str] = []

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del receive
        delegated.append(dict(scope["headers"])[b"host"].decode())
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = CloudflareAccessMiddleware(
        app, authenticator, required_hosts=frozenset({"mcp.example.com"})
    )
    await middleware(
        {"type": "http", "headers": [(b"host", b"mcp.example.com")]},  # type: ignore[arg-type]
        receive,
        send,
    )
    assert sent[0]["status"] == 401
    assert delegated == []

    sent.clear()
    await middleware(
        {"type": "http", "headers": [(b"host", b"mcp-lan.example.com")]},  # type: ignore[arg-type]
        receive,
        send,
    )
    assert sent[0]["status"] == 204
    assert delegated == ["mcp-lan.example.com"]


async def test_mcp_verifier_uses_bound_cloudflare_identity() -> None:
    verifier = MCPTokenVerifier(TokenAuthenticator.from_json("", "pepper"))
    principal = Principal("cloudflare:owner@example.com", frozenset({Scope.READ}))
    with bind_cloudflare_principal(principal):
        token = await verifier.verify_token("opaque-edge-token")
    assert token is not None
    assert token.client_id == principal.id
    assert token.scopes == [Scope.READ]


def test_cloudflare_settings_fail_closed() -> None:
    with pytest.raises(ValidationError, match="Cloudflare Access requires"):
        Settings(environment="test", cloudflare_access_enabled=True)
    with pytest.raises(ValidationError, match="cannot receive knowledge:admin"):
        Settings(
            environment="test",
            cloudflare_access_enabled=True,
            cloudflare_access_issuer_url="https://example.cloudflareaccess.com",
            cloudflare_access_audience="audience",
            cloudflare_access_allowed_emails="owner@example.com",
            cloudflare_access_scopes="knowledge:admin",
        )
