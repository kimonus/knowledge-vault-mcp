"""Real process death at durable controller boundaries; no Docker or user data."""

import hashlib
import hmac
import json
import multiprocessing
import os
import signal
import stat
from unittest.mock import Mock

import pytest
from test_standalone_docker_controller import controller as c

CTX = multiprocessing.get_context("fork")


def load(path):
    instance = object.__new__(c.Instance)
    instance.path = path
    instance.config = json.loads(c.read_private(path / "config.json"))
    instance.docker = ["docker"]
    return instance


def prepare(path):
    path.chmod(0o700)
    token, pepper = "synthetic-device", "synthetic-pepper"
    payload = dict.fromkeys(c.CREDENTIAL_FILES, "synthetic-fixture")
    payload.update(
        {
            "device-token": token,
            "token-pepper": pepper,
            "bootstrap-tokens": json.dumps(
                [
                    {
                        "principal_id": "standalone-device",
                        "scopes": ["knowledge:read", "knowledge:write"],
                        "sha256": hmac.new(
                            pepper.encode(), token.encode(), hashlib.sha256
                        ).hexdigest(),
                    }
                ]
            ),
        }
    )
    generation = "a" * 32
    c.create_generation(path, generation, payload)
    config = {
        "state": "ready",
        "app_image": "sha256:" + "a" * 64,
        "credential_generation": generation,
        "credential_volume": "original",
    }
    c.private_file(path / "config.json", json.dumps(config).encode())
    c.private_file(path / "original", json.dumps(payload).encode())
    return load(path)


def fake_engine(instance, monkeypatch):
    def seed(owner, payload):
        volume = "volume-" + str(owner.config["configuration_generation"])
        c.atomic_private(owner.path / volume, json.dumps(payload).encode())
        return volume

    def runtime(**kwargs):
        assert instance.credentials() == json.loads(
            c.read_private(instance.path / instance.config["credential_volume"])
        )
        instance.phase("verifying")

    monkeypatch.setattr(c, "seed_credentials", seed)
    monkeypatch.setattr(instance, "compose", Mock(return_value=b""))
    monkeypatch.setattr(instance, "quiesce", lambda: instance.phase("quiesced"))
    monkeypatch.setattr(instance, "start_runtime", runtime)


def kill_rotation(path, boundary, retire, connection):
    with pytest.MonkeyPatch.context() as patch, c.lifecycle_lock(path):
        instance = load(path)
        fake_engine(instance, patch)
        phase = instance.phase
        private = c.private_file
        replace = c.os.replace

        def stopped():
            connection.send("boundary")
            signal.pause()

        def phase_hook(name, **metadata):
            phase(name, **metadata)
            if boundary == name:
                stopped()

        def private_hook(file, data):
            private(file, data)
            if boundary == "file:" + file.name and file.parent.name != "a" * 32:
                stopped()

        def replace_hook(source, target):
            selected = json.loads(source.read_bytes()).get("operation", {}).get("phase")
            if target.name == "config.json" and selected == "credentials-selected":
                if boundary == "before-selection-replace":
                    stopped()
                replace(source, target)
                if boundary == "after-selection-replace":
                    stopped()
            else:
                replace(source, target)

        patch.setattr(instance, "phase", phase_hook)
        patch.setattr(c, "private_file", private_hook)
        patch.setattr(c.os, "replace", replace_hook)
        instance.rotate(retire_previous=retire)


@pytest.mark.parametrize("retire", [False, True])
@pytest.mark.parametrize(
    "boundary",
    [
        "quiesced",
        "creating-generation",
        *["file:" + name for name in c.CREDENTIAL_FILES],
        "file:complete.json",
        "seeding-generation",
        "generation-prepared",
        "before-selection-replace",
        "after-selection-replace",
        "credentials-selected",
        "verifying",
    ],
)
def test_sigkill_rotation_is_forward_recoverable(tmp_path, monkeypatch, boundary, retire):
    instance = prepare(tmp_path)
    fake_engine(instance, monkeypatch)
    # Retirement starts from an overlap generation with a newer current token.
    instance.rotate()
    before = instance.credentials()
    receive, send = CTX.Pipe(duplex=False)
    process = CTX.Process(target=kill_rotation, args=(tmp_path, boundary, retire, send))
    process.start()
    try:
        assert receive.poll(10), "child did not reach the requested transition"
        assert receive.recv() == "boundary"
        os.kill(process.pid, signal.SIGKILL)
        process.join(10)
        assert process.exitcode == -signal.SIGKILL
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)
        receive.close()
        send.close()
    interrupted = load(tmp_path)
    assert interrupted.config["state"] == "rotation-in-progress"
    selected = interrupted.config["operation"].get("selected_generation")
    fake_engine(interrupted, monkeypatch)
    with c.lifecycle_lock(tmp_path):
        interrupted.recover()
    after = interrupted.credentials()
    assert interrupted.config["state"] == "ready"
    assert after["token-pepper"] == before["token-pepper"]
    if selected:
        assert interrupted.config["credential_generation"] == selected
    records = json.loads(after["bootstrap-tokens"])
    if retire:
        assert after["device-token"] == before["device-token"]
        assert len(records) == 1
    else:
        assert len(records) == len(json.loads(before["bootstrap-tokens"])) + 1
    assert after == json.loads(c.read_private(tmp_path / interrupted.config["credential_volume"]))


def lock_holder(path, connection):
    with c.lifecycle_lock(path):
        instance = load(path)
        instance.config["operator_marker"] = "new-generation"
        instance.save()
        connection.send("locked")
        connection.recv()


def test_lock_precedes_configuration_load_in_another_process(tmp_path, monkeypatch):
    prepare(tmp_path)
    parent, child = CTX.Pipe()
    process = CTX.Process(target=lock_holder, args=(tmp_path, child))
    process.start()
    try:
        assert parent.poll(10) and parent.recv() == "locked"
        constructor = Mock(side_effect=AssertionError("configuration loaded before lock"))
        monkeypatch.setattr(c, "Instance", constructor)
        monkeypatch.setattr("sys.argv", ["docker.py", "--state", str(tmp_path), "status"])
        assert c.main() == 1
        constructor.assert_not_called()
        parent.send("release")
        process.join(10)
        assert process.exitcode == 0
        monkeypatch.undo()
        with c.lifecycle_lock(tmp_path):
            current = load(tmp_path)
            assert current.config["operator_marker"] == "new-generation"
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)
        parent.close()
        child.close()


def test_atomic_save_syncs_file_before_replace_then_parent(tmp_path, monkeypatch):
    events = []
    sync, replace = os.fsync, os.replace

    def fsync(fd):
        events.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        sync(fd)

    def swap(source, target):
        events.append("replace")
        replace(source, target)

    monkeypatch.setattr(c.os, "fsync", fsync)
    monkeypatch.setattr(c.os, "replace", swap)
    c.atomic_private(tmp_path / "config.json", b"{}")
    assert events == ["file", "replace", "directory"]


@pytest.mark.parametrize("migration", [False, True])
def test_upgrade_recovery_never_assumes_partial_migration_completed(
    tmp_path, monkeypatch, migration
):
    instance = prepare(tmp_path)
    fake_engine(instance, monkeypatch)
    monkeypatch.setattr(c, "call", Mock(return_value=b""))
    calls = []

    def backup(*args):
        assert instance.config["state"] == "upgrade-in-progress"
        assert instance.config["operation"]["phase"] == "creating-backup"
        calls.append("backup")
        if not migration:
            raise c.Failed("backup unavailable")
        return "b" * 64

    def migrate(**kwargs):
        assert instance.config["pre_upgrade_snapshot"] == "b" * 64
        instance.phase("migrating", migration_started=True)
        raise c.Failed("subprocess may have committed")

    instance.repository = backup
    instance.start_runtime = migrate
    with pytest.raises(c.Failed):
        instance.upgrade("sha256:" + "b" * 64, tmp_path, tmp_path / "key")
    persisted = load(tmp_path)
    assert persisted.config["state"] == "upgrade-failed"
    assert calls == ["backup"]
    assert bool(persisted.config["operation"].get("migration_started")) is migration
    if migration:
        fake_engine(persisted, monkeypatch)
        with pytest.raises(c.Failed, match="NEW instance"):
            persisted.recover()


@pytest.mark.parametrize(
    "heartbeat, accepted", [(99, False), (100, False), (101, True), (50, False)]
)
def test_worker_requires_post_restart_fresh_success(tmp_path, monkeypatch, heartbeat, accepted):
    instance = prepare(tmp_path)
    instance.container = Mock(return_value="worker")
    instance.inspect = Mock(return_value={"State": {"Status": "running"}})
    instance.worker_clock = Mock(return_value=(102, heartbeat))
    if accepted:
        instance.wait_worker(100, timeout=0)
    else:
        with pytest.raises(c.Failed, match="fresh successful"):
            instance.wait_worker(100, timeout=0)


def killed_upgrade(path, boundary, connection):
    with pytest.MonkeyPatch.context() as patch, c.lifecycle_lock(path):
        instance = load(path)
        fake_engine(instance, patch)
        patch.setattr(c, "call", Mock(return_value=b""))
        instance.repository = Mock(return_value="b" * 64)
        phase = instance.phase

        def checkpoint(name, **metadata):
            phase(name, **metadata)
            if name == boundary:
                connection.send("boundary")
                signal.pause()

        def runtime(**kwargs):
            instance.phase("migrating", migration_started=True)
            instance.phase("migration-complete", migration_completed=True)
            instance.phase("verifying")

        instance.phase = checkpoint
        instance.start_runtime = runtime
        instance.upgrade("sha256:" + "b" * 64, path, path / "key")


@pytest.mark.parametrize(
    "boundary",
    [
        "quiesced",
        "creating-backup",
        "backup-verified",
        "candidate-selected",
        "migrating",
        "migration-complete",
        "verifying",
    ],
)
def test_sigkill_upgrade_never_appears_completed(tmp_path, monkeypatch, boundary):
    prepare(tmp_path)
    receive, send = CTX.Pipe(duplex=False)
    process = CTX.Process(target=killed_upgrade, args=(tmp_path, boundary, send))
    process.start()
    try:
        assert receive.poll(10)
        assert receive.recv() == "boundary"
        process.kill()
        process.join(10)
        assert process.exitcode == -signal.SIGKILL
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)
        receive.close()
        send.close()
    instance = load(tmp_path)
    assert instance.config["state"] == "upgrade-in-progress"
    fake_engine(instance, monkeypatch)
    with pytest.raises(c.Failed, match="Interrupted"):
        instance.start()
    operation = instance.config["operation"]
    if boundary == "migrating":
        assert operation["migration_started"]
        assert operation["recovery_snapshot"] == "b" * 64
        with pytest.raises(c.Failed, match="NEW instance"):
            instance.recover()
    elif boundary in ("migration-complete", "verifying"):
        assert operation["migration_completed"]
        instance.repository = Mock(side_effect=AssertionError("forward recovery must not restore"))
        instance.recover()
        instance.repository.assert_not_called()
        assert instance.config["state"] == "ready"
        assert instance.config["app_image"] == "sha256:" + "b" * 64
    else:
        monkeypatch.setattr(c, "call", Mock(return_value=b""))
        instance.repository = Mock(return_value="c" * 64)
        instance.recover(tmp_path, tmp_path / "key")
        assert instance.config["state"] == "ready"
        assert instance.config["pre_upgrade_snapshot"] == "c" * 64


def test_failed_durability_prevents_first_side_effect(tmp_path, monkeypatch):
    instance = prepare(tmp_path)
    compose = Mock()
    instance.compose = compose
    monkeypatch.setattr(c.os, "fsync", Mock(side_effect=OSError("simulated storage failure")))
    with pytest.raises(OSError):
        instance.start()
    compose.assert_not_called()
    assert load(tmp_path).config["state"] == "ready"


def test_partial_volume_seeding_is_never_selected(tmp_path, monkeypatch):
    instance = prepare(tmp_path)
    fake_engine(instance, monkeypatch)
    seed = c.seed_credentials
    original_generation = instance.config["credential_generation"]

    def partial(owner, payload):
        # Model a seed subprocess which wrote some files before termination.
        c.private_file(owner.path / "incomplete-orphan-volume", b"partial synthetic volume")
        raise c.Failed("seed subprocess interrupted")

    monkeypatch.setattr(c, "seed_credentials", partial)
    with pytest.raises(c.Failed, match="interrupted"):
        instance.rotate(retire_previous=True)
    recovered = load(tmp_path)
    assert recovered.config["credential_generation"] == original_generation
    assert recovered.config["credential_volume"] == "original"
    fake_engine(recovered, monkeypatch)
    monkeypatch.setattr(c, "seed_credentials", seed)
    with c.lifecycle_lock(tmp_path):
        recovered.recover()
    assert recovered.config["credential_volume"] != "incomplete-orphan-volume"
    assert recovered.credentials()["token-pepper"] == "synthetic-pepper"


@pytest.mark.parametrize("owner,status", [("different-instance", "exited"), ("fixture", "running")])
def test_quiescence_refuses_foreign_or_live_writers(tmp_path, owner, status):
    instance = prepare(tmp_path)
    instance.begin("upgrade")
    instance.config["project"] = "fixture"
    instance.compose = Mock(return_value=b"writer-id")
    instance.inspect = Mock(
        return_value={
            "Config": {"Labels": {"com.docker.compose.project": owner}},
            "State": {"Status": status},
        }
    )
    with pytest.raises(c.Failed):
        instance.quiesce()
    assert instance.config["operation"]["phase"] == "stopping-writers"


def test_recovery_refuses_disagreeing_credential_journal(tmp_path, monkeypatch):
    instance = prepare(tmp_path)
    fake_engine(instance, monkeypatch)
    instance.begin("rotation", retire_previous=True, selected_generation="b" * 32)
    with pytest.raises(c.Failed, match="refusing rollback"):
        instance.recover()
    assert instance.config["credential_generation"] == "a" * 32
    assert instance.config["state"] == "rotation-failed"


@pytest.mark.parametrize("seal", [False, True])
def test_tampered_generation_never_reaches_writers(tmp_path, monkeypatch, seal):
    instance = prepare(tmp_path)
    fake_engine(instance, monkeypatch)
    c.atomic_private(instance.secret_dir / "device-token", b"different-synthetic-device")
    with pytest.raises(c.Failed, match="changed" if seal else "disagree"):
        c.credential_payload(instance.secret_dir, sealed=seal)


@pytest.mark.parametrize("kind", ["start", "recreate", "init", "restore"])
def test_recovery_obeys_operation_kind_without_implicit_migration(tmp_path, monkeypatch, kind):
    instance = prepare(tmp_path)
    fake_engine(instance, monkeypatch)
    instance.begin(kind)
    runtime = Mock()
    instance.start_runtime = runtime
    if kind in ("start", "recreate"):
        instance.recover()
        runtime.assert_called_once_with(migrate=False)
        assert instance.config["state"] == "ready"
    else:
        with pytest.raises(c.Failed, match="NEW state directory"):
            instance.recover()
        runtime.assert_not_called()


def test_healthy_state_is_not_a_recovery_request(tmp_path):
    with pytest.raises(c.Failed, match="No interrupted"):
        prepare(tmp_path).recover()


def test_legacy_full_identity_cannot_restart_without_a_manifest(tmp_path):
    instance = prepare(tmp_path)
    instance.config["model_dir"] = str(tmp_path)
    with pytest.raises(c.Failed, match="fresh restore"):
        instance.check_model()


def test_stopped_worker_cannot_supply_readiness(tmp_path):
    instance = prepare(tmp_path)
    instance.container = Mock(return_value="worker")
    instance.inspect = Mock(return_value={"State": {"Status": "exited"}})
    with pytest.raises(c.Failed, match="not running"):
        instance.wait_worker(0)


def test_mutable_upgrade_image_is_refused_before_any_operation(tmp_path, monkeypatch):
    instance = prepare(tmp_path)
    call = Mock()
    monkeypatch.setattr(c, "call", call)
    with pytest.raises(c.Failed, match="immutable"):
        instance.upgrade("app:latest", tmp_path, tmp_path / "key")
    call.assert_not_called()
    assert instance.config["state"] == "ready"


@pytest.mark.parametrize("identifier,owner", [("short", "fixture"), ("a" * 64, "other")])
def test_container_ownership_is_required_for_lifecycle_acceptance(tmp_path, identifier, owner):
    instance = prepare(tmp_path)
    instance.config["project"] = "fixture"
    instance.compose = Mock(return_value=identifier.encode())
    instance.inspect = Mock(
        return_value={
            "Config": {
                "Labels": {
                    "com.docker.compose.project": owner,
                    "com.docker.compose.service": "worker",
                }
            }
        }
    )
    with pytest.raises(c.Failed):
        instance.container("worker")


def test_lock_refuses_a_symlink_or_nonprivate_inode(tmp_path):
    tmp_path.chmod(0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path)
    with pytest.raises(c.Failed, match="symlinks"), c.lifecycle_lock(alias):
        pytest.fail("unsafe lock acquired")
    lock = tmp_path / ".lock"
    lock.write_text("")
    lock.chmod(0o644)
    with pytest.raises(c.Failed, match="Unsafe lifecycle"), c.lifecycle_lock(tmp_path):
        pytest.fail("unsafe lock acquired")


def test_pre_migration_recovery_requires_explicit_repository_credentials(tmp_path):
    instance = prepare(tmp_path)
    instance.begin("upgrade", candidate_app_image="sha256:" + "b" * 64)
    with pytest.raises(c.Failed, match="repository and password-file"):
        instance.recover()


def test_legacy_and_invalid_generation_paths_are_unambiguous(tmp_path):
    instance = prepare(tmp_path)
    instance.config.pop("credential_generation")
    (tmp_path / "secrets").mkdir(mode=0o700)
    assert instance.secret_dir == tmp_path / "secrets"
    instance.config["credential_generation"] = "../outside"
    with pytest.raises(c.Failed, match="Invalid credential generation"):
        _ = instance.secret_dir


def test_changed_engine_identity_cannot_load_an_instance(tmp_path, monkeypatch):
    instance = prepare(tmp_path)
    monkeypatch.setattr(c.Instance, "engine_id", Mock(return_value="different-engine"))
    with pytest.raises(c.Failed, match="Engine identity differs"):
        c.Instance(tmp_path, instance.config | {"context": "fixture", "engine_id": "original"})
