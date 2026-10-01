import hashlib
import hmac
import json

import pytest
from pydantic import ValidationError

from knowledge_vault.auth.rate_limit import RateLimit, RateLimitGuard, SlidingWindowRateLimiter
from knowledge_vault.auth.tokens import Scope, TokenAuthenticator, hash_token
from knowledge_vault.config import Settings
from knowledge_vault.services.errors import RateLimitedError


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


def test_bootstrap_tokens_accept_one_record_or_a_list_and_fail_clearly() -> None:
    record = {"principal_id": "me", "sha256": hash_token("t", "p").hex(), "scopes": [Scope.READ]}
    single = TokenAuthenticator.from_json(json.dumps(record), "p")
    listed = TokenAuthenticator.from_json(json.dumps([record, record]), "p")
    assert single.authenticate("t") is not None
    assert listed.authenticate("t") is not None
    assert TokenAuthenticator.from_json("  ", "p").authenticate("t") is None

    for raw, message in [
        ("not json", "valid JSON"),
        ('["text"]', "JSON object"),
        ('{"principal_id": "me"}', "needs principal_id"),
        (json.dumps({**record, "sha256": "zz"}), "needs principal_id"),
        (json.dumps({**record, "sha256": "abcd"}), "32-byte"),
        (json.dumps({**record, "scopes": ["knowledge:everything"]}), "known scopes"),
        (json.dumps({**record, "principal_id": " "}), "non-empty"),
    ]:
        with pytest.raises(ValueError, match=message):
            TokenAuthenticator.from_json(raw, "p")


def test_rate_limit_guard_reports_when_to_retry() -> None:
    limiter = SlidingWindowRateLimiter()
    policy = RateLimit(1, 10)
    assert limiter.retry_after("me", "read", policy, now=100) is None
    assert limiter.retry_after("me", "read", policy, now=104) == 6

    guard = RateLimitGuard(Settings(environment="test", rate_admin_per_minute=1))
    guard.enforce("me", Scope.ADMIN)
    guard.enforce("someone-else", Scope.ADMIN)
    guard.enforce("me", Scope.READ)
    with pytest.raises(RateLimitedError, match="admin rate limit exceeded") as excinfo:
        guard.enforce("me", Scope.ADMIN)
    assert 1 <= excinfo.value.retry_after_seconds <= 60


def test_environment_must_be_a_known_value() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="prod")  # type: ignore[arg-type]
