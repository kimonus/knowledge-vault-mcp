import json
import time
from dataclasses import replace

import jwt
import pytest
from knowledge_vault_dashboard.auth.tokens import Scope, TokenRecord, hash_token
from knowledge_vault_dashboard.config import Settings, load_settings
from knowledge_vault_dashboard.sessions import LIFETIME, Sessions

from conftest import CREDENTIAL, PEPPER, SERVICE


def test_session_revocation_and_key_rotation(settings):
    sessions = Sessions(settings)
    cookie = sessions.issue(CREDENTIAL)
    assert cookie
    assert sessions.verify(cookie)
    assert sessions.issue("wrong") is None
    assert sessions.verify(cookie[:-2] + "xx") is None
    assert sessions.verify("bad") is None
    assert Sessions(replace(settings, signing_key="rotated-key-" * 4)).verify(cookie) is None
    record = settings.records[0]
    replacement = replace(record, digest=hash_token("new-browser-credential", PEPPER))
    assert Sessions(replace(settings, records=(replacement,))).verify(cookie) is None
    renamed = replace(record, principal_id="other-browser")
    assert Sessions(replace(settings, records=(renamed,))).verify(cookie) is None
    # Overlapping records preserve sessions only while the original remains enabled.
    rotating = Sessions(replace(settings, records=(record, replacement)))
    assert rotating.verify(cookie)
    assert rotating.verify(rotating.issue("new-browser-credential"))
    claims = jwt.decode(cookie, options={"verify_signature": False})
    assert record.digest.hex() not in json.dumps(claims)


@pytest.mark.parametrize(
    "override",
    [
        {"exp": int(time.time()) - 1, "iat": int(time.time()) - LIFETIME - 1},
        {"iat": int(time.time()) + 20, "exp": int(time.time()) + 1820},
        {"exp": int(time.time()) + 3600},
        {"nonce": "short"},
        {"nonce": 1},
        {"ref": None},
        {"ref": "wrong"},
        {"aud": "other.test"},
        {"exp": "invalid"},
        {"iat": 1.0},
        {"exp": 100.0},
    ],
)
def test_signed_invalid_claims(settings, override):
    sessions = Sessions(settings)
    claims = jwt.decode(sessions.issue(CREDENTIAL), options={"verify_signature": False})
    claims.update(override)
    assert sessions.verify(jwt.encode(claims, settings.signing_key, algorithm="HS256")) is None


@pytest.mark.parametrize("field", ["sub", "aud", "iat", "exp", "nonce", "ref"])
def test_missing_claims(settings, field):
    sessions = Sessions(settings)
    claims = jwt.decode(sessions.issue(CREDENTIAL), options={"verify_signature": False})
    claims.pop(field)
    assert sessions.verify(jwt.encode(claims, settings.signing_key, algorithm="HS256")) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"host": ""},
        {"host": "core-private.test"},
        {"host": "bad/host"},
        {"core_host": ""},
        {"core_url": "http://attacker.test"},
        {"core_url": "https://user:pass@core.test"},
        {"core_url": "https://core.test/path"},
        {"core_url": "https://core.test?url=attacker"},
        {"core_url": "https://core.test#fragment"},
        {"core_url": "ftp://core.test"},
        {"service_token": ""},
        {"service_token": "bad\r\nHeader: injected"},
        {"pepper": "short"},
        {"signing_key": "short"},
        {"signing_key": PEPPER},
        {"pepper": SERVICE},
        {"records": ()},
        {"mode": "typo"},
    ],
)
def test_fail_closed_settings(settings, changes):
    with pytest.raises(ValueError):
        replace(settings, **changes)


@pytest.mark.parametrize(
    "scopes",
    [
        frozenset(),
        frozenset({Scope.WRITE}),
        frozenset({Scope.ADMIN}),
        frozenset({Scope.READ, Scope.WRITE}),
        frozenset({Scope.READ, Scope.ADMIN}),
    ],
)
def test_all_browser_records_require_exact_read(settings, scopes):
    with pytest.raises(ValueError):
        replace(settings, records=(replace(settings.records[0], scopes=scopes),))


def test_no_service_token_reuse_or_bad_record(settings):
    for record in [
        replace(settings.records[0], digest=hash_token(SERVICE, PEPPER)),
        replace(settings.records[0], digest=b"bad"),
        replace(settings.records[0], principal_id=""),
        replace(settings.records[0], principal_id="p" * 129),
    ]:
        with pytest.raises(ValueError):
            replace(settings, records=(record,))
    assert SERVICE not in repr(settings)
    assert PEPPER not in repr(settings)
    assert Settings().enabled is False


@pytest.mark.parametrize(
    "changes",
    [
        {"access_issuer": "http://team.cloudflareaccess.com"},
        {"access_issuer": "https://attacker.test"},
        {"access_issuer": "https://team.cloudflareaccess.com/path"},
        {"access_audience": ""},
        {"access_emails": frozenset()},
        {"pepper": PEPPER},
        {"signing_key": "x" * 32},
    ],
)
def test_access_configuration_has_no_fallback(access_settings, changes):
    with pytest.raises(ValueError):
        replace(access_settings, **changes)


def test_secret_file_refs_only(settings, tmp_path):
    data = {
        "enabled": True,
        "host": settings.host,
        "core_url": settings.core_url,
        "core_host": settings.core_host,
    }
    for field in ["service_token", "pepper", "signing_key"]:
        secret = tmp_path / field
        secret.write_text(getattr(settings, field))
        data[field + "_file"] = str(secret)
    record = settings.records[0]
    records = tmp_path / "records"
    records.write_text(
        json.dumps(
            [
                {
                    "principal_id": record.principal_id,
                    "sha256": record.digest.hex(),
                    "scopes": ["knowledge:read"],
                }
            ]
        )
    )
    data["records_file"] = str(records)
    config = tmp_path / "config.json"
    config.write_text(json.dumps(data))
    assert load_settings(config) == settings
    data["service_token"] = SERVICE
    config.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="file references"):
        load_settings(config)
    config.write_text(json.dumps({"enabled": False, "access_emails": ["owner@example.test"]}))
    assert load_settings(config).access_emails == frozenset({"owner@example.test"})


def test_entrypoint_disabled_by_default(monkeypatch):
    from knowledge_vault_dashboard import main

    seen = {}
    monkeypatch.setattr("sys.argv", ["knowledge-vault-dashboard"])
    monkeypatch.setattr(main.uvicorn, "run", lambda app, **kwargs: seen.update(kwargs))
    main.run()
    assert seen["proxy_headers"] is False
    assert seen["access_log"] is False
    assert seen["server_header"] is False


def test_token_auth_helpers(settings):
    from knowledge_vault_dashboard.auth.tokens import Principal, TokenAuthenticator

    auth = TokenAuthenticator(settings.records, settings.pepper)
    auth.validate_inspection_principals(frozenset({"browser"}))
    for principal in ["missing", "cloudflare:owner"]:
        with pytest.raises(ValueError):
            auth.validate_inspection_principals(frozenset({principal}))
    assert Principal("test", frozenset({Scope.ADMIN})).permits(Scope.READ)
    assert not Principal("test", frozenset()).permits(Scope.READ)
    assert not TokenAuthenticator.from_json("", "pepper").records
    for raw in [
        "{",
        "[1]",
        '[{"principal_id":"x"}]',
        '[{"principal_id":"", "sha256":"00", "scopes":[]}]',
        '[{"principal_id":"x", "sha256":"00", "scopes":[]}]',
    ]:
        with pytest.raises(ValueError):
            TokenAuthenticator.from_json(raw, "pepper")
    ordinary = TokenRecord("ordinary", settings.records[0].digest, frozenset({Scope.READ}))
    with pytest.raises(ValueError):
        TokenAuthenticator(
            (*settings.records, ordinary), settings.pepper
        ).validate_inspection_principals(frozenset({"browser"}))
