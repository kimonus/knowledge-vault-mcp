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
from knowledge_vault.auth.hosts import canonical_host
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
    # Access enabled with a published host but no private-host allowlist is refused, because a
    # bearer token would otherwise be accepted for every unpublished Host.
    valid = dict(
        environment="test",
        cloudflare_access_enabled=True,
        cloudflare_access_issuer_url="https://example.cloudflareaccess.com",
        cloudflare_access_audience="audience",
        cloudflare_access_allowed_emails="owner@example.com",
        cloudflare_access_public_hosts="mcp.example.com",
    )
    with pytest.raises(ValidationError, match="private hostname allowlist"):
        Settings(**valid)  # type: ignore[arg-type]
    # With the allowlist set it validates; a host cannot be both public and private.
    assert Settings(**valid, cloudflare_access_private_hosts="mcp-lan.example.com")  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="both a Cloudflare public and a private host"):
        Settings(**valid, cloudflare_access_private_hosts="mcp.example.com")  # type: ignore[arg-type]


def test_canonical_host_folds_equivalent_spellings() -> None:
    base = canonical_host("mcp.example.com")
    assert base == "mcp.example.com"
    for variant in (
        "MCP.EXAMPLE.COM",
        "mcp.example.com.",
        " mcp.example.com ",
        "mcp.example.com:443",
        "mcp．example．com",  # fullwidth full stops
        "mcp.example.com。",  # ideographic full stop
        "ＭＣＰ.example.com",  # fullwidth "MCP"
    ):
        assert canonical_host(variant) == base, variant
    assert canonical_host("[::1]:8000") == "[::1]"


async def test_private_host_allowlist_confines_the_bearer_surface() -> None:
    authenticator, _ = make_authenticator()
    middleware = CloudflareAccessMiddleware(
        _passthrough,
        authenticator,
        required_hosts=frozenset({"mcp.example.com"}),
        private_hosts=frozenset({"mcp-lan.example.com"}),
    )
    # The one declared private host falls through to bearer authentication (204 passthrough).
    assert await _status(middleware, [(b"host", b"mcp-lan.example.com")]) == 204
    # Every other unpublished Host is refused before bearer auth is ever reached.
    for host in (b"localhost", b"evil.example", b"knowledge-vault-api"):
        assert await _status(middleware, [(b"host", host)]) == 401, host
    # The published host still demands a signed assertion.
    assert await _status(middleware, [(b"host", b"mcp.example.com")]) == 401


async def _status(
    middleware: CloudflareAccessMiddleware,
    headers: list[tuple[bytes, bytes]],
    path: str = "/api/v1/search",
) -> int:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await middleware({"type": "http", "path": path, "headers": headers}, receive, send)  # type: ignore[arg-type]
    return sent[0]["status"]


async def _passthrough(scope: dict[str, Any], receive: Any, send: Any) -> None:
    del scope, receive
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def test_host_spelling_cannot_bypass_the_assertion_requirement() -> None:
    authenticator, private_key = make_authenticator()
    middleware = CloudflareAccessMiddleware(
        _passthrough, authenticator, required_hosts=frozenset({"MCP.example.com"})
    )
    for host in (
        b"mcp.example.com",
        b"MCP.EXAMPLE.COM",
        b"mcp.example.com:443",
        b"mcp.example.com.",
        b" mcp.example.com.:8000 ",
    ):
        assert await _status(middleware, [(b"host", host)]) == 401, host
    assert await _status(middleware, [(b"host", b"lan.example.com")]) == 204
    # A missing, repeated, or undecodable Host cannot be classified, so it is refused.
    assert await _status(middleware, []) == 400
    assert (
        await _status(middleware, [(b"host", b"lan.example.com"), (b"host", b"mcp.example.com")])
        == 400
    )
    assert await _status(middleware, [(b"host", "żółw.example".encode())]) == 400
    # Health probes address the Pod by IP and may omit Host entirely.
    assert await _status(middleware, [], path="/health/live") == 204
    assert await _status(middleware, [(b"host", b"mcp.example.com")], path="/health/live") == 401
    # A valid assertion is honoured on any host; more than one assertion never is.
    token = assertion(private_key).encode()
    assert (
        await _status(
            middleware, [(b"host", b"mcp.example.com"), (b"cf-access-jwt-assertion", token)]
        )
        == 204
    )
    assert (
        await _status(
            middleware, [(b"cf-access-jwt-assertion", token), (b"cf-access-jwt-assertion", token)]
        )
        == 401
    )
    assert (
        await _status(
            middleware, [(b"host", b"lan.example.com"), (b"cf-access-jwt-assertion", b"\xff")]
        )
        == 401
    )


async def test_private_host_allowlist_requires_assertions_everywhere_else() -> None:
    authenticator, _ = make_authenticator()
    middleware = CloudflareAccessMiddleware(
        _passthrough,
        authenticator,
        required_hosts=frozenset({"mcp.example.com"}),
        private_hosts=frozenset({"mcp-lan.example.com"}),
    )
    assert await _status(middleware, [(b"host", b"mcp-lan.example.com:8443")]) == 204
    assert await _status(middleware, [(b"host", b"knowledge-vault:8000")]) == 401
    assert await _status(middleware, [(b"host", b"10.0.0.7:8000")]) == 401
    assert await _status(middleware, [(b"host", b"10.0.0.7:8000")], path="/health/ready") == 204


def test_cloudflare_settings_require_a_real_published_hostname() -> None:
    base = {
        "environment": "test",
        "cloudflare_access_enabled": True,
        "cloudflare_access_issuer_url": "https://example.cloudflareaccess.com",
        "cloudflare_access_audience": "audience",
        "cloudflare_access_allowed_emails": "owner@example.com",
    }
    with pytest.raises(ValidationError, match="published hostname"):
        Settings(**base)  # public_base_url is still the .invalid placeholder
    derived = Settings(
        **base,
        public_base_url="https://MCP.Example.com./",
        cloudflare_access_private_hosts="mcp-lan.example.com",
    )
    assert derived.cloudflare_access_public_host_set == frozenset({"mcp.example.com"})
    explicit = Settings(
        **base,
        cloudflare_access_public_hosts="mcp.example.com, alt.example.com:443",
        cloudflare_access_private_hosts="mcp-lan.example.com",
    )
    assert explicit.cloudflare_access_public_host_set == {"mcp.example.com", "alt.example.com"}
    assert explicit.cloudflare_access_private_host_set == {"mcp-lan.example.com"}
    with pytest.raises(ValidationError, match="both"):
        Settings(
            **base,
            public_base_url="https://mcp.example.com",
            cloudflare_access_private_hosts="mcp.example.com",
        )


async def test_jwks_refresh_is_throttled_for_unknown_key_ids() -> None:
    _, private_key = make_authenticator()
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk["kid"] = "test"
    requests = 0

    def handler(request: Request) -> Response:
        nonlocal requests
        requests += 1
        return Response(200, json={"keys": [jwk]})

    client = CloudflareJWKSClient(
        "https://example.cloudflareaccess.com/cdn-cgi/access/certs",
        transport=MockTransport(handler),
    )
    assert await client.get_signing_key(assertion(private_key)) is not None
    for index in range(25):
        forged = jwt.encode(
            {"sub": "x"}, private_key, algorithm="RS256", headers={"kid": f"unknown-{index}"}
        )
        with pytest.raises(CloudflareAccessError, match="unknown"):
            await client.get_signing_key(forged)
    assert requests == 1
    # The known key keeps working while unknown IDs are refused.
    assert await client.get_signing_key(assertion(private_key)) is not None

    rotating = CloudflareJWKSClient(
        "https://example.cloudflareaccess.com/cdn-cgi/access/certs",
        transport=MockTransport(handler),
        min_refresh_interval=0,
    )
    for index in range(3):
        forged = jwt.encode(
            {"sub": "x"}, private_key, algorithm="RS256", headers={"kid": f"rotated-{index}"}
        )
        with pytest.raises(CloudflareAccessError, match="unknown"):
            await rotating.get_signing_key(forged)
    assert requests == 4
