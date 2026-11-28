"""R-019: the durable migration boundary precedes any external gateway exposure."""

import json
from unittest.mock import Mock

import pytest
from test_standalone_interruption import c, fake_engine, prepare


def runtime_fixture(tmp_path, monkeypatch):
    instance = prepare(tmp_path)
    instance.begin(
        "upgrade", candidate_app_image=instance.config["app_image"], upgrade_recovery="forward-v1"
    )
    instance.wait = Mock()
    instance.check_model = Mock()
    instance.worker_clock = Mock(return_value=(100, 0))
    instance.wait_worker = Mock()
    instance.verify = Mock()
    return instance


def test_migration_success_is_durable_before_gateway_subprocess(tmp_path, monkeypatch):
    instance = runtime_fixture(tmp_path, monkeypatch)
    commands = []

    def compose(*args, **kwargs):
        commands.append(args)
        stored = json.loads((tmp_path / "config.json").read_bytes())
        if "migrate" in args:
            assert stored["operation"]["migration_started"]
            assert not stored["operation"].get("migration_completed")
        if args == ("up", "-d", "gateway"):
            assert stored["operation"]["migration_completed"]
            assert stored["state"] == "upgrade-in-progress"
        return b""

    instance.compose = compose
    instance.start_runtime(migrate=True)
    assert ("up", "-d", "gateway") in commands
    instance.wait_worker.assert_called_once_with(100)
    instance.verify.assert_called_once_with(worker_after=100)
    assert instance.config["state"] == "upgrade-in-progress"


def test_gateway_is_not_exposed_after_lost_migration_reply(tmp_path, monkeypatch):
    instance = runtime_fixture(tmp_path, monkeypatch)
    commands = []

    def compose(*args, **kwargs):
        commands.append(args)
        if "migrate" in args:
            raise c.Failed("lost subprocess reply")
        return b""

    instance.compose = compose
    with pytest.raises(c.Failed, match="lost subprocess reply"):
        instance.start_runtime(migrate=True)
    assert not instance.config["operation"].get("migration_completed")
    assert ("up", "-d", "gateway") not in commands


def test_gateway_requires_durable_migration_before_forward_start(tmp_path, monkeypatch):
    instance = runtime_fixture(tmp_path, monkeypatch)
    instance.compose = Mock(return_value=b"")
    with pytest.raises(c.Failed, match="durable successful migration"):
        instance.start_runtime(migrate=False)
    assert ("up", "-d", "gateway") not in [call.args for call in instance.compose.call_args_list]


@pytest.mark.parametrize("phase", ["migration-complete", "starting-gateway", "verifying"])
def test_forward_recovery_preserves_candidate_database_without_restore(
    tmp_path, monkeypatch, phase
):
    instance = runtime_fixture(tmp_path, monkeypatch)
    instance.config["database_volume"] = "candidate-data-with-acknowledged-writes"
    instance.phase(phase, migration_started=True, migration_completed=True)
    fake_engine(instance, monkeypatch)
    instance.repository = Mock(side_effect=AssertionError("snapshot rollback would lose writes"))
    instance.start_runtime = Mock()
    instance.recover()
    instance.start_runtime.assert_called_once_with(migrate=False)
    instance.repository.assert_not_called()
    assert instance.config["database_volume"] == "candidate-data-with-acknowledged-writes"
    assert instance.config["state"] == "ready"


def test_failed_forward_recovery_stays_forward_only(tmp_path, monkeypatch):
    instance = runtime_fixture(tmp_path, monkeypatch)
    instance.phase("verifying", migration_started=True, migration_completed=True)
    fake_engine(instance, monkeypatch)
    instance.start_runtime = Mock(side_effect=c.Failed("candidate unavailable"))
    with pytest.raises(c.Failed):
        instance.recover()
    assert instance.config["state"] == "upgrade-failed"
    assert json.loads((tmp_path / "config.json").read_bytes())["operation"]["migration_completed"]
    instance.start_runtime = Mock()
    instance.recover()
    assert instance.config["state"] == "ready"


def test_legacy_interrupted_upgrade_never_recommends_snapshot_rollback(tmp_path, monkeypatch):
    instance = runtime_fixture(tmp_path, monkeypatch)
    instance.config["operation"].pop("upgrade_recovery")
    instance.phase("verifying", migration_started=True)
    fake_engine(instance, monkeypatch)
    with pytest.raises(c.Failed, match="preserve the candidate database") as error:
        instance.recover()
    assert "not snapshot rollback" in str(error.value)
    assert not instance.config["operation"].get("migration_completed")
