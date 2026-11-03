"""Safety checks for the disposable client evaluation lab."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "quality_client_lab.py"
spec = importlib.util.spec_from_file_location("quality_client_lab", SCRIPT)
assert spec is not None and spec.loader is not None
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


@pytest.mark.parametrize(
    "key", ["KNOWLEDGE_VAULT_DATABASE_URL", "DATABASE_URL", "PGHOST", "DOCKER_HOST", "CODEX_HOME"]
)
def test_refuses_inherited_configuration(key: str) -> None:
    with pytest.raises(RuntimeError, match="unsafe inherited configuration"):
        lab.refuse_inherited_configuration({key: "production-value"})


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://user:secret@production.example/db",
        "postgresql+psycopg://lab:secret@127.0.0.1:5432/production",
    ],
)
def test_migration_and_reset_refuse_nonlab_url(url: str) -> None:
    with pytest.raises(RuntimeError, match="disposable lab database"):
        lab.migrate(url)
    with pytest.raises(RuntimeError, match="non-lab database"):
        lab.reset_and_verify(url)


def test_cleanup_only_owned_resources() -> None:
    calls: list[tuple[str, ...]] = []

    def fake_docker(*args: str) -> str:
        calls.append(args)
        if args[:2] == ("network", "create"):
            return "network-id"
        if args[:2] == ("run", "-d"):
            raise RuntimeError("interrupted startup")
        if args[:2] == ("inspect", "kv-client-lab-db"):
            return "other-owner"
        if args[:2] == ("network", "inspect"):
            return "other-owner"
        return ""

    with (
        patch.object(lab, "docker", fake_docker),
        pytest.raises(RuntimeError, match="interrupted startup"),
        lab.disposable_postgres(),
    ):
        pass
    assert not any(call[0] == "rm" or call[:2] == ("network", "rm") for call in calls)


def test_generated_credentials_have_no_admin_scope_or_secret_in_settings_repr() -> None:
    settings, token = lab.credentials("postgresql+psycopg://lab:generated@127.0.0.1:54321/lab")
    assert token not in repr(settings)
    assert "knowledge:admin" not in settings.bootstrap_tokens
    assert settings.embeddings_enabled is False


def test_manifest_does_not_include_url_or_token() -> None:
    source = SCRIPT.read_text()
    assert '"database_url": url' not in source
    assert '"token": token' not in source


def test_reset_verifies_empty_counts() -> None:
    url = "postgresql+psycopg://lab:generated@/lab?host=/tmp/kv-client-lab-test"

    class Connection:
        def execute(self, _statement: object) -> None:
            return None

        def scalar(self, _statement: object) -> int:
            return 0

    class Engine:
        def begin(self):
            class Context:
                def __enter__(self):
                    return Connection()

                def __exit__(self, *_args: object) -> None:
                    return None

            return Context()

        def dispose(self):
            return None

    lab.ACTIVE_URLS.add(url)
    try:
        with patch.object(lab, "create_engine", return_value=Engine()):
            lab.reset_and_verify(url)
    finally:
        lab.ACTIVE_URLS.discard(url)


def test_cleanup_refuses_foreign_container(tmp_path: Path) -> None:
    owner = "a" * 32
    socket_dir = tmp_path / f"kv-client-lab-{owner}-socket"
    (tmp_path / "environment.json").write_text(
        json.dumps({"lab_owner": owner, "db_socket_dir": str(socket_dir)})
    )
    with pytest.raises(RuntimeError, match="invalid lab socket directory"):
        lab.cleanup_interrupted(tmp_path)


def test_cleanup_after_interruption_is_owner_scoped(tmp_path: Path) -> None:
    owner = "b" * 32
    socket_dir = Path(f"/tmp/kv-client-lab-{owner}-missing")  # noqa: S108
    (tmp_path / "environment.json").write_text(
        json.dumps({"lab_owner": owner, "db_socket_dir": str(socket_dir)})
    )
    token = tmp_path / "lab-token"
    token.write_text("synthetic")
    calls: list[tuple[str, ...]] = []

    def fake_docker(*args: str) -> str:
        calls.append(args)
        return ""

    with (
        patch.object(lab, "docker", fake_docker),
        patch.object(lab, "owned_resource", return_value=True),
    ):
        lab.cleanup_interrupted(tmp_path)
    assert ("rm", "-f", "-v", f"kv-client-lab-db-{owner}") in calls
    assert not token.exists()
