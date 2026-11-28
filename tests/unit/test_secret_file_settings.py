import contextlib
import os
import runpy
from pathlib import Path

import pytest
from alembic import context
from alembic.config import Config
from pydantic import ValidationError

from knowledge_vault.config import Settings, get_settings

FIELDS = ("DATABASE_URL", "TOKEN_PEPPER", "BOOTSTRAP_TOKENS")


@pytest.fixture(autouse=True)
def isolated_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in FIELDS:
        monkeypatch.delenv(f"KNOWLEDGE_VAULT_{name}", raising=False)
        monkeypatch.delenv(f"KNOWLEDGE_VAULT_{name}_FILE", raising=False)
    get_settings.cache_clear()


def test_file_inputs_preserve_exact_values_and_hide_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = ("postgresql://fixture.invalid/example%25", " pepper with newline\n", "[]\n")
    for name, value in zip(FIELDS, values, strict=True):
        path = tmp_path / name
        path.write_text(value)
        monkeypatch.setenv(f"KNOWLEDGE_VAULT_{name}_FILE", str(path))
    settings = Settings(environment="test")
    assert (settings.database_url, settings.token_pepper, settings.bootstrap_tokens) == values
    assert (
        settings.database_url_file
        is settings.token_pepper_file
        is settings.bootstrap_tokens_file
        is None
    )
    assert str(tmp_path) not in repr(settings)
    assert all(value not in repr(settings) for value in values)
    assert not any(key.endswith("_file") for key in settings.model_dump())
    # Migration's Settings() and server/worker get_settings() share the same adapter.
    assert get_settings().database_url == settings.database_url
    get_settings.cache_clear()


@pytest.mark.parametrize("name", FIELDS)
@pytest.mark.parametrize("direct", ["", "fixture"])
def test_direct_and_file_conflict_even_when_direct_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, direct: str
) -> None:
    path = tmp_path / "private-path"
    path.write_text("fixture")
    monkeypatch.setenv(f"KNOWLEDGE_VAULT_{name}", direct)
    monkeypatch.setenv(f"KNOWLEDGE_VAULT_{name}_FILE", str(path))
    with pytest.raises(RuntimeError, match="mutually exclusive") as exc:
        Settings(environment="test")
    assert str(path) not in str(exc.value)
    assert direct not in str(exc.value) if direct else True


def test_constructor_value_conflicts_with_environment_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "file"
    path.write_text("fixture")
    monkeypatch.setenv("KNOWLEDGE_VAULT_TOKEN_PEPPER_FILE", str(path))
    with pytest.raises(RuntimeError, match="mutually exclusive"):
        Settings(environment="test", token_pepper="")


@pytest.mark.parametrize(
    "kind", ["missing", "directory", "symlink", "fifo", "invalid_utf8", "large", "nul", "empty"]
)
def test_invalid_file_inputs_fail_closed_without_disclosing_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    path = tmp_path / "sensitive-path"
    if kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        target = tmp_path / "target"
        target.write_text("fixture")
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "invalid_utf8":
        path.write_bytes(b"\xff")
    elif kind == "large":
        path.write_bytes(b"x" * (128 * 1024 + 1))
    elif kind == "nul":
        path.write_bytes(b"x\0y")
    elif kind == "empty":
        path.write_bytes(b"")
    monkeypatch.setenv("KNOWLEDGE_VAULT_TOKEN_PEPPER_FILE", str(path))
    with pytest.raises(RuntimeError, match="TOKEN_PEPPER_FILE cannot be read") as exc:
        Settings(environment="test")
    assert str(path) not in str(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None


def test_validation_error_and_repr_do_not_show_file_values_or_paths(tmp_path: Path) -> None:
    path = tmp_path / "private-path"
    path.write_text("synthetic-private-value")
    with pytest.raises(ValidationError) as exc:
        Settings(environment="test", token_pepper_file=str(path), standalone_mode=True)
    assert str(path) not in str(exc.value)
    assert "synthetic-private-value" not in str(exc.value)
    assert str(path) not in repr(exc.value.errors())
    assert "synthetic-private-value" not in repr(exc.value.errors())
    assert exc.value.__context__ is None
    for detail in exc.value.errors():
        nested = detail.get("ctx", {}).get("error")
        if isinstance(nested, BaseException):
            assert nested.__context__ is None


def test_malformed_webhook_validation_hides_authority() -> None:
    marker = "synthetic-private-authority"
    with pytest.raises(ValidationError) as exc:
        Settings(environment="test", alert_webhook_url=f"https://{marker}＠example.com")
    assert marker not in str(exc.value)
    assert marker not in repr(exc.value.errors())
    assert exc.value.__context__ is None
    for detail in exc.value.errors():
        nested = detail.get("ctx", {}).get("error")
        if isinstance(nested, BaseException):
            assert nested.__context__ is None


@pytest.mark.parametrize("field", ["cloudflare_access_scopes", "public_base_url"])
def test_cloudflare_validation_errors_hide_values(field: str) -> None:
    marker = "synthetic-private-marker"
    values = {
        "cloudflare_access_enabled": True,
        "cloudflare_access_issuer_url": "https://team.cloudflareaccess.com",
        "cloudflare_access_audience": "audience",
        "cloudflare_access_allowed_emails": "user@example.com",
        "cloudflare_access_private_hosts": "private.example.com",
    }
    values[field] = marker if field == "cloudflare_access_scopes" else f"https://{marker}＠host"
    with pytest.raises(ValidationError) as exc:
        Settings(environment="test", **values)
    assert marker not in str(exc.value)
    assert marker not in repr(exc.value.errors())
    assert exc.value.__context__ is None
    for detail in exc.value.errors():
        nested = detail.get("ctx", {}).get("error")
        if isinstance(nested, BaseException):
            assert nested.__context__ is None


def test_explicit_dotenv_and_disabled_dotenv_are_isolated(tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.write_text(" exact pepper\n")
    dotenv = tmp_path / "explicit.env"
    dotenv.write_text(f"KNOWLEDGE_VAULT_TOKEN_PEPPER_FILE={secret}\n")
    assert Settings(environment="test", _env_file=dotenv).token_pepper == " exact pepper\n"
    assert Settings(environment="test", _env_file=None).token_pepper == ""


def test_dotenv_direct_and_environment_file_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = tmp_path / "secret"
    secret.write_text("file value")
    dotenv = tmp_path / "explicit.env"
    dotenv.write_text("KNOWLEDGE_VAULT_TOKEN_PEPPER=direct-value\n")
    monkeypatch.setenv("KNOWLEDGE_VAULT_TOKEN_PEPPER_FILE", str(secret))
    with pytest.raises(RuntimeError, match="mutually exclusive"):
        Settings(environment="test", _env_file=dotenv)


def test_unreadable_file_error_does_not_expose_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "private-path"
    monkeypatch.setenv("KNOWLEDGE_VAULT_TOKEN_PEPPER_FILE", str(path))

    def denied(*args: object, **kwargs: object) -> int:
        raise PermissionError(f"cannot access {path}")

    monkeypatch.setattr("knowledge_vault.config.os.open", denied)
    with pytest.raises(RuntimeError) as exc:
        Settings(environment="test")
    assert str(path) not in str(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None


def test_alembic_direct_settings_reads_database_file_without_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_file = tmp_path / "database-url"
    database_file.write_text("postgresql://example.invalid/db%25")
    monkeypatch.setenv("KNOWLEDGE_VAULT_DATABASE_URL_FILE", str(database_file))
    config = Config()
    monkeypatch.setattr(context, "config", config, raising=False)
    monkeypatch.setattr(context, "is_offline_mode", lambda: True)
    configured: dict[str, object] = {}
    monkeypatch.setattr(context, "configure", lambda **kwargs: configured.update(kwargs))
    monkeypatch.setattr(context, "begin_transaction", contextlib.nullcontext)
    monkeypatch.setattr(context, "run_migrations", lambda: None)
    runpy.run_path(str(Path(__file__).resolve().parents[2] / "migrations/env.py"))
    assert configured["url"] == "postgresql+psycopg://example.invalid/db%25"


def test_default_and_direct_settings_preserve_existing_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KNOWLEDGE_VAULT_TOKEN_PEPPER", "direct fixture")
    assert Settings(environment="test").token_pepper == "direct fixture"
    assert Settings(environment="test", token_pepper="init fixture").token_pepper == "init fixture"
    assert (
        Settings(environment="test").database_url
        == "postgresql://knowledge_vault@localhost/knowledge_vault"
    )
