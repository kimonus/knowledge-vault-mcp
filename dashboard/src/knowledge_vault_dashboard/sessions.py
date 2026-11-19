"""Stateless, absolute-lifetime sessions with per-request credential revocation."""

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

import jwt

from .auth.tokens import TokenAuthenticator, TokenRecord
from .config import Settings

COOKIE = "__Host-kv_dashboard"
LIFETIME = 1800


@dataclass(frozen=True)
class Identity:
    principal: str
    expires: int
    nonce: str


class Sessions:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.authenticator = TokenAuthenticator(settings.records, settings.pepper)

    def reference(self, record: TokenRecord) -> str:
        value = record.principal_id.encode() + b"\x00" + record.digest
        return hmac.new(self.settings.signing_key.encode(), value, hashlib.sha256).hexdigest()

    def issue(self, credential: str) -> str | None:
        principal = self.authenticator.authenticate(credential)
        if principal is None:
            return None
        # Bind to the matched rotating record, not just its principal.
        from .auth.tokens import hash_token

        digest = hash_token(credential, self.settings.pepper)
        record = next(r for r in self.settings.records if hmac.compare_digest(digest, r.digest))
        now = int(time.time())
        return jwt.encode(
            {
                "sub": principal.id,
                "aud": self.settings.host,
                "iat": now,
                "exp": now + LIFETIME,
                "nonce": secrets.token_urlsafe(24),
                "ref": self.reference(record),
            },
            self.settings.signing_key,
            algorithm="HS256",
        )

    def verify(self, cookie: str) -> Identity | None:
        if len(cookie) > 4096:
            return None
        try:
            claims = jwt.decode(
                cookie,
                self.settings.signing_key,
                algorithms=["HS256"],
                audience=self.settings.host,
                options={"require": ["sub", "aud", "iat", "exp", "nonce", "ref"]},
            )
            if (
                type(claims["iat"]) is not int
                or type(claims["exp"]) is not int
                or claims["exp"] - claims["iat"] != LIFETIME
                or not isinstance(claims["nonce"], str)
                or len(claims["nonce"]) != 32
                or not isinstance(claims["ref"], str)
                or not any(
                    r.principal_id == claims["sub"]
                    and hmac.compare_digest(self.reference(r), claims["ref"])
                    for r in self.settings.records
                )
            ):
                return None
            return Identity(claims["sub"], claims["exp"], claims["nonce"])
        except (jwt.PyJWTError, ValueError, TypeError, RecursionError):
            return None

    def csrf(self, identity: Identity) -> str:
        message = f"{identity.principal}\x00{identity.expires}\x00{identity.nonce}"
        return hmac.new(
            self.settings.signing_key.encode(), message.encode(), hashlib.sha256
        ).hexdigest()
