import asyncio
import time
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Protocol

import httpx
import jwt
from jwt.exceptions import PyJWTError

from knowledge_vault.auth.tokens import Principal, Scope


class CloudflareAccessError(Exception):
    """Raised when a Cloudflare Access assertion cannot be trusted."""


class _SigningKeyProvider(Protocol):
    async def get_signing_key(self, token: str) -> Any: ...


_cloudflare_principal: ContextVar[Principal | None] = ContextVar(
    "cloudflare_access_principal", default=None
)


def current_cloudflare_principal() -> Principal | None:
    return _cloudflare_principal.get()


@contextmanager
def bind_cloudflare_principal(principal: Principal) -> Generator[None]:
    token = _cloudflare_principal.set(principal)
    try:
        yield
    finally:
        _cloudflare_principal.reset(token)


class CloudflareAccessAuthenticator:
    """Validate Access JWT assertions attached by Cloudflare's edge."""

    def __init__(
        self,
        *,
        issuer_url: str,
        audience: str,
        allowed_emails: frozenset[str],
        scopes: frozenset[Scope],
        signing_keys: _SigningKeyProvider | None = None,
    ) -> None:
        self._issuer_url = issuer_url.rstrip("/")
        self._audience = audience
        self._allowed_emails = frozenset(email.casefold() for email in allowed_emails)
        self._scopes = scopes
        self._signing_keys = signing_keys or CloudflareJWKSClient(
            f"{self._issuer_url}/cdn-cgi/access/certs"
        )

    async def authenticate(self, assertion: str) -> Principal:
        try:
            signing_key = await self._signing_keys.get_signing_key(assertion)
            return self._authenticate(assertion, signing_key)
        except CloudflareAccessError:
            raise
        except (PyJWTError, ValueError, TypeError, KeyError) as exc:
            raise CloudflareAccessError("Cloudflare Access assertion is invalid") from exc

    def _authenticate(self, assertion: str, signing_key: Any) -> Principal:
        claims: dict[str, Any] = jwt.decode(
            assertion,
            signing_key,
            algorithms=["RS256"],
            audience=self._audience,
            issuer=self._issuer_url,
            options={"require": ["aud", "email", "exp", "iat", "iss", "sub"]},
        )
        email_claim = claims.get("email")
        if not isinstance(email_claim, str) or not email_claim.strip():
            raise CloudflareAccessError("Cloudflare Access assertion has no email identity")
        email = email_claim.strip().casefold()
        if email not in self._allowed_emails:
            raise CloudflareAccessError("Cloudflare Access identity is not allowed")
        return Principal(id=f"cloudflare:{email}", scopes=self._scopes)


class CloudflareJWKSClient:
    """Small async JWKS cache that refreshes for a newly rotated key ID.

    Refreshes are spaced by `min_refresh_interval`, so assertions carrying unknown key IDs
    cannot make the origin fetch the key set on every request.
    """

    def __init__(
        self,
        url: str,
        *,
        lifespan: float = 300,
        timeout: float = 5,
        min_refresh_interval: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = url
        self._lifespan = lifespan
        self._timeout = timeout
        self._min_refresh_interval = min_refresh_interval
        self._transport = transport
        self._keys: dict[str, Any] = {}
        self._expires_at = 0.0
        self._last_refresh_attempt = float("-inf")
        self._lock = asyncio.Lock()

    async def get_signing_key(self, token: str) -> Any:
        header = jwt.get_unverified_header(token)
        key_id = header.get("kid")
        if not isinstance(key_id, str) or not key_id:
            raise CloudflareAccessError("Cloudflare Access assertion has no key ID")
        now = time.monotonic()
        key = self._keys.get(key_id)
        if key is not None and now < self._expires_at:
            return key
        async with self._lock:
            now = time.monotonic()
            key = self._keys.get(key_id)
            if key is not None and now < self._expires_at:
                return key
            if now - self._last_refresh_attempt < self._min_refresh_interval:
                raise CloudflareAccessError("Cloudflare Access signing key is unknown")
            self._last_refresh_attempt = now
            await self._refresh()
            key = self._keys.get(key_id)
            if key is None:
                raise CloudflareAccessError("Cloudflare Access signing key is unknown")
            return key

    async def _refresh(self) -> None:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await client.get(self._url)
                response.raise_for_status()
                payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("JWKS body is not an object")
            key_set = jwt.PyJWKSet.from_dict(payload)
            keys = {
                key.key_id: key.key
                for key in key_set.keys
                if isinstance(key.key_id, str) and key.key_id
            }
            if not keys:
                raise ValueError("JWKS contains no signing keys")
        except (httpx.HTTPError, PyJWTError, ValueError, TypeError, KeyError) as exc:
            raise CloudflareAccessError("Cloudflare Access signing keys are unavailable") from exc
        self._keys = keys
        self._expires_at = time.monotonic() + self._lifespan
