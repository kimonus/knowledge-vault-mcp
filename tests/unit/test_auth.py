import hashlib
import hmac
import json

from knowledge_vault.auth.rate_limit import RateLimit, SlidingWindowRateLimiter
from knowledge_vault.auth.tokens import Scope, TokenAuthenticator, hash_token


def test_token_authentication_and_scope() -> None:
    pepper = "pepper"
    token = "opaque-token-value"
    raw = json.dumps(
        [
            {
                "principal_id": "me",
                "sha256": hash_token(token, pepper).hex(),
                "scopes": [Scope.READ],
            }
        ]
    )
    authenticator = TokenAuthenticator.from_json(raw, pepper)
    principal = authenticator.authenticate(token)
    assert principal is not None
    assert principal.permits(Scope.READ)
    assert not principal.permits(Scope.WRITE)
    assert authenticator.authenticate("wrong") is None


def test_hash_token_is_hmac_sha256() -> None:
    expected = hmac.new(b"p", b"t", hashlib.sha256).digest()
    assert hash_token("t", "p") == expected


def test_admin_scope_permits_all_operations() -> None:
    raw = '[{"principal_id":"me","sha256":"' + ("00" * 32) + '","scopes":["knowledge:admin"]}]'
    auth = TokenAuthenticator.from_json(raw, "p")
    assert auth._records[0].scopes == frozenset({Scope.ADMIN})


def test_sliding_window_rate_limit() -> None:
    limiter = SlidingWindowRateLimiter()
    policy = RateLimit(2, 10)
    assert limiter.allow("me", "read", policy, now=1)
    assert limiter.allow("me", "read", policy, now=2)
    assert not limiter.allow("me", "read", policy, now=3)
    assert limiter.allow("me", "read", policy, now=12)
    assert limiter.allow("other", "read", policy, now=3)
