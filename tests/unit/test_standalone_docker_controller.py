"""Safety boundaries of the local Docker lifecycle controller."""

import importlib.util
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "standalone_controller", ROOT / "deploy/standalone/docker.py"
)
assert spec and spec.loader
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


def test_tool_failure_never_relays_secret_output(monkeypatch):
    result = subprocess.CompletedProcess([], 9, b"private assertion", b"password=private-value")
    monkeypatch.setattr(controller.subprocess, "run", Mock(return_value=result))
    with pytest.raises(controller.Failed, match="exit 9") as failure:
        controller.call(["docker", "run"])
    assert "private" not in str(failure.value)
    assert failure.value.__context__ is None


def test_private_paths_and_files_fail_closed(tmp_path):
    tmp_path.chmod(0o700)
    assert controller.private_directory(tmp_path) == tmp_path
    key = tmp_path / "key"
    controller.private_file(key, b"private fixture")
    assert controller.read_private(key) == b"private fixture"
    with pytest.raises(FileExistsError):
        controller.private_file(key, b"replacement")
    key.chmod(0o644)
    with pytest.raises(controller.Failed):
        controller.read_private(key)
    link = tmp_path / "link"
    link.symlink_to(key)
    with pytest.raises(controller.Failed):
        controller.read_private(link)
    tmp_path.chmod(0o755)
    with pytest.raises(controller.Failed):
        controller.private_directory(tmp_path)


def test_ambient_compose_selection_is_removed_and_restored(monkeypatch):
    monkeypatch.setenv("COMPOSE_FILE", "/unrelated/production.yaml")
    monkeypatch.setenv("KV_DATABASE_VOLUME", "unrelated-data")
    monkeypatch.setenv("DOCKER_CONFIG", "/private/docker-config")
    with pytest.raises(RuntimeError), controller.clean_compose_environment():
        assert "COMPOSE_FILE" not in os.environ
        assert "KV_DATABASE_VOLUME" not in os.environ
        assert os.environ["DOCKER_CONFIG"] == "/private/docker-config"
        raise RuntimeError
    assert os.environ["KV_DATABASE_VOLUME"] == "unrelated-data"


def test_fresh_storage_uses_engine_allocation_not_a_caller_name(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    instance = object.__new__(controller.Instance)
    instance.path, instance.docker, instance.config = tmp_path, ["docker"], {"project": "fixture"}
    invoke = Mock(return_value=b"a" * 64)
    monkeypatch.setattr(controller, "call", invoke)
    monkeypatch.setattr(instance, "save", Mock())
    monkeypatch.setattr(
        instance,
        "inspect",
        Mock(
            return_value={
                "Mounts": [
                    {"Destination": "/data", "Type": "volume", "Name": "engine-allocated-volume"},
                ]
            }
        ),
    )
    assert instance.fresh_volume("sha256:" + "b" * 64, "/data") == "engine-allocated-volume"
    command = invoke.call_args.args[0]
    assert command[command.index("--mount") + 1] == "type=volume,dst=/data"
    assert "--network" in command and "none" in command
    assert instance.config["anchors"] == ["a" * 64]


@pytest.mark.parametrize("state", ["initializing", "restoring"])
def test_partial_instances_cannot_start_writers(state):
    instance = object.__new__(controller.Instance)
    instance.config = {"state": state}
    instance.compose = Mock()
    with pytest.raises(controller.Failed):
        instance.start()
    instance.compose.assert_not_called()


def test_restore_rejects_implicit_snapshot_before_any_engine_call(monkeypatch, tmp_path):
    invoke = Mock()
    monkeypatch.setattr(controller, "call", invoke)
    monkeypatch.setattr(
        "sys.argv",
        [
            "docker.py",
            "--state",
            str(tmp_path / "new"),
            "restore",
            "--snapshot",
            "latest",
            "--repository",
            str(tmp_path),
            "--password-file",
            str(tmp_path / "key"),
            "--app-image",
            "sha256:" + "a" * 64,
            "--backup-image",
            "sha256:" + "b" * 64,
        ],
    )
    assert controller.main() == 1
    invoke.assert_not_called()


def test_failed_upgrade_stops_writers_and_records_recovery_image(monkeypatch, tmp_path):
    import json

    tmp_path.chmod(0o700)
    previous, candidate = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    config = {"state": "ready", "app_image": previous, "credential_volume": "old"}
    controller.private_file(tmp_path / "config.json", json.dumps(config).encode())
    instance = object.__new__(controller.Instance)
    instance.path, instance.config, instance.docker = tmp_path, config, ["docker"]
    instance.quiesce = Mock()
    instance.compose = Mock()
    instance.repository = Mock(return_value="c" * 64)
    instance.start_runtime = Mock(side_effect=controller.Failed("migration refused"))
    monkeypatch.setattr(controller, "call", Mock())
    with pytest.raises(controller.Failed, match="migration refused"):
        instance.upgrade(candidate, tmp_path / "repository", tmp_path / "key")
    assert config["state"] == "upgrade-failed"
    assert config["previous_app_image"] == previous
    assert config["pre_upgrade_snapshot"] == "c" * 64
    assert instance.compose.call_args.args == ("stop", "gateway", "api", "worker")
    assert json.loads((tmp_path / "config.json").read_bytes())["state"] == "upgrade-failed"


def test_restore_refuses_another_member_on_target_network(monkeypatch, tmp_path):
    tmp_path.chmod(0o700)
    config = {"state": "restoring", "project": "fixture", "database_volume": "fresh"}
    instance = Mock(path=tmp_path, config=config)
    instance.container.return_value = "db-id"
    instance.inspect.side_effect = [
        {"Containers": {"db-id": {}, "other-writer": {}}},
        {"Mounts": [{"Name": "fresh", "Destination": "/var/lib/postgresql/data"}]},
    ]
    monkeypatch.setattr(controller, "initialize", Mock(return_value=instance))
    monkeypatch.setattr(
        "sys.argv",
        [
            "docker.py",
            "--state",
            str(tmp_path / "new"),
            "restore",
            "--snapshot",
            "a" * 64,
            "--repository",
            str(tmp_path / "repository"),
            "--password-file",
            str(tmp_path / "key"),
            "--app-image",
            "sha256:" + "b" * 64,
            "--backup-image",
            "sha256:" + "c" * 64,
        ],
    )
    assert controller.main() == 1
    instance.repository.assert_not_called()
    instance.start.assert_not_called()
