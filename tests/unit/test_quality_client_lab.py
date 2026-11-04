"""Safety checks for the disposable client evaluation lab."""

import asyncio
import fcntl
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "quality_client_lab.py"
sys.path.insert(0, str(SCRIPT.parent))
import m1_offline_model as offline_model  # noqa: E402

spec = importlib.util.spec_from_file_location("quality_client_lab", SCRIPT)
assert spec is not None and spec.loader is not None
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


def independent_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    root = tmp_path / "independent"
    root.mkdir()
    monkeypatch.setattr(offline_model, "INDEPENDENT_ROOT", root)
    snapshot = root / ("a" * 40)
    snapshot.mkdir()
    files = {
        "modules.json": b"[]",
        "config.json": b"{}",
        "tokenizer_config.json": b"{}",
        "model.safetensors": b"synthetic weights",
    }
    for name, content in files.items():
        (snapshot / name).write_bytes(content)
    manifest = root / f"{snapshot.name}.json"
    manifest.write_text(
        json.dumps(
            {
                "model_id": offline_model.MODEL_ID,
                "revision": snapshot.name,
                "dimensions": 384,
                "files": {
                    name: hashlib.sha256(content).hexdigest() for name, content in files.items()
                },
            }
        )
    )
    return snapshot, manifest


def test_verified_independent_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, manifest = independent_snapshot(tmp_path, monkeypatch)
    result = offline_model.verify_snapshot(snapshot, manifest)
    assert result["revision"] == "a" * 40
    assert result["file_count"] == 4


@pytest.mark.parametrize("damage", ["missing", "changed", "symlink", "wrong_dimension"])
def test_incomplete_or_unsafe_snapshot_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    snapshot, manifest = independent_snapshot(tmp_path, monkeypatch)
    if damage == "missing":
        (snapshot / "modules.json").unlink()
    elif damage == "changed":
        (snapshot / "model.safetensors").write_bytes(b"modified")
    elif damage == "symlink":
        (snapshot / "outside").symlink_to(tmp_path)
    else:
        content = json.loads(manifest.read_text())
        content["dimensions"] = 768
        manifest.write_text(json.dumps(content))
    with pytest.raises(RuntimeError):
        offline_model.verify_snapshot(snapshot, manifest)


def test_offline_environment_uses_empty_cache(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    with offline_model.offline_environment(cache):
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
        assert os.environ["HF_HOME"] == str(cache)
        assert not list(cache.iterdir())
    assert "HF_HOME" not in os.environ


def test_semantic_mode_requires_verified_model_before_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        lab,
        "check_host",
        lambda: {
            "memory_available_bytes": 8 * 1024**3,
            "load_average_1m": 0.1,
        },
    )
    with (
        patch.object(lab, "disposable_postgres") as database,
        pytest.raises(RuntimeError, match="independent M1 model root"),
    ):
        asyncio.run(
            lab.run(
                "semantic-smoke",
                None,
                model_path=Path("/absent"),
                model_manifest=Path("/absent.json"),
            )
        )
    database.assert_not_called()


def test_semantic_capacity_rejects_pressure() -> None:
    with pytest.raises(RuntimeError, match="5 GiB"):
        lab.check_semantic_capacity({"memory_available_bytes": 4 * 1024**3, "load_average_1m": 0})


@pytest.mark.parametrize(
    "counts,success",
    [((1, 1, 0), True), ((1, 0, 1), False), ((1, 1, 1), False), ((0, 0, 0), False)],
)
def test_semantic_index_must_be_complete(counts: tuple[int, int, int], success: bool) -> None:
    class Connection:
        def __init__(self) -> None:
            self.values = iter(counts)

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def scalar(self, _query: object) -> int:
            return next(self.values)

    class Engine:
        def connect(self) -> Connection:
            return Connection()

        def dispose(self) -> None:
            pass

    url = "lab-only"
    lab.ACTIVE_URLS.add(url)
    try:
        with patch.object(lab, "create_engine", return_value=Engine()):
            if success:
                assert lab.require_complete_index(url, 1, "model")["ready"] == 1
            else:
                with pytest.raises(RuntimeError, match="indexing incomplete"):
                    lab.require_complete_index(url, 1, "model")
    finally:
        lab.ACTIVE_URLS.discard(url)


def test_semantic_retrieval_rejects_lexical_or_degraded_fallback() -> None:
    hit = {"assertion": {"id": "vector-id"}}
    lab.require_vector_retrieval({"results": [hit], "embedding_degraded": False}, "vector-id", 0)
    for results, degraded, lexical in [([hit], False, 1), ([hit], True, 0), ([], False, 0)]:
        with pytest.raises(RuntimeError):
            lab.require_vector_retrieval(
                {"results": results, "embedding_degraded": degraded}, "vector-id", lexical
            )


async def test_semantic_model_load_failure_never_starts_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, manifest = independent_snapshot(tmp_path, monkeypatch)
    monkeypatch.setattr(
        lab,
        "check_host",
        lambda: {
            "memory_available_bytes": 8 * 1024**3,
            "load_average_1m": 0.1,
        },
    )

    class BrokenProvider:
        def __init__(
            self, _path: str, _dimensions: int, *, allow_download: bool, retry_after_seconds: float
        ) -> None:
            assert not allow_download and retry_after_seconds == 0

        async def embed(self, _texts: list[str]) -> None:
            raise RuntimeError("model initialization failed")

    with (
        patch.object(lab, "SentenceTransformerProvider", BrokenProvider),
        patch.object(lab, "disposable_postgres") as database,
        pytest.raises(RuntimeError, match="model initialization failed"),
    ):
        await lab.run("semantic-smoke", None, model_path=snapshot, model_manifest=manifest)
    database.assert_not_called()


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


def test_cleanup_only_owned_resources(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_docker(*args: str) -> str:
        calls.append(args)
        return ""

    with (
        patch.object(lab, "docker", fake_docker),
        patch.object(lab, "default_smoke_output", side_effect=lambda owner: tmp_path / owner),
        patch.object(lab, "start_watcher", side_effect=RuntimeError("interrupted startup")),
        pytest.raises(RuntimeError, match="interrupted startup"),
        lab.disposable_postgres(),
    ):
        pass
    assert not any(call[0] == "rm" for call in calls)


@pytest.mark.parametrize("cancelled", [False, True])
def test_disposable_postgres_waits_for_launcher_cleanup(tmp_path: Path, cancelled: bool) -> None:
    socket_dir: Path | None = None
    waited = False

    def fake_docker(*args: str) -> str:
        if args[0] == "inspect" and "{{.HostConfig.NetworkMode}}" in args:
            return "none"
        if args[0] == "inspect" and "{{json .Mounts}}" in args:
            return json.dumps([{"Source": str(socket_dir), "Destination": "/var/run/postgresql"}])
        return ""

    class Ready:
        returncode = 0

    class FakeWatcher:
        def __init__(self, owner: str, socket: Path) -> None:
            self.owner = owner
            self.socket = socket

        def wait(self, *, timeout: float) -> int:
            nonlocal waited
            assert timeout == 120
            waited = True
            lab.cleanup_owned(self.owner, self.socket, None)
            return 0

    def fake_start(owner: str, socket: Path, _output: Path | None, _password: str):
        nonlocal socket_dir
        socket_dir = socket
        descriptor = os.open(socket / ".parent-lock", os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        return descriptor, FakeWatcher(owner, socket)

    with (
        patch.object(lab, "docker", fake_docker),
        patch.object(lab, "default_smoke_output", side_effect=lambda owner: tmp_path / owner),
        patch.object(lab.subprocess, "run", return_value=Ready()),
        patch.object(lab, "start_watcher", side_effect=fake_start),
        patch.object(lab, "wait_for_launch"),
    ):
        if cancelled:
            with pytest.raises(asyncio.CancelledError), lab.disposable_postgres() as (url, *_rest):
                assert url in lab.ACTIVE_URLS
                raise asyncio.CancelledError
        else:
            with lab.disposable_postgres() as (url, *_rest):
                assert url in lab.ACTIVE_URLS
    assert socket_dir is not None and not socket_dir.exists()
    assert waited


def test_startup_signal_waits_for_launcher_cleanup(tmp_path: Path) -> None:
    started = False
    waited = False

    def fake_start(owner: str, socket: Path, _output: Path | None, _password: str):
        nonlocal started
        descriptor = os.open(socket / ".parent-lock", os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)

        class FakeWatcher:
            def wait(self, *, timeout: float) -> int:
                nonlocal waited
                waited = True
                lab.cleanup_owned(owner, socket, None)
                return 0

        return descriptor, FakeWatcher()

    def mark_started(*_args: object) -> None:
        nonlocal started
        started = True

    with (
        patch.object(lab, "docker", return_value=""),
        patch.object(lab, "default_smoke_output", side_effect=lambda owner: tmp_path / owner),
        patch.object(lab, "start_watcher", side_effect=fake_start),
        patch.object(lab, "wait_for_launch", side_effect=mark_started),
        pytest.raises(lab.LabInterrupted),
        lab.disposable_postgres(lambda: started),
    ):
        pass
    assert waited


@pytest.mark.parametrize("interrupted", [False, True])
def test_readiness_probe_timeout_is_bounded_and_releases_parent_lock(
    tmp_path: Path, interrupted: bool
) -> None:
    calls: list[float] = []
    cleaned = False
    stop = threading.Event()

    class FakeWatcher:
        def __init__(self, owner: str, socket: Path, output: Path) -> None:
            self.owner, self.socket, self.output = owner, socket, output

        def wait(self, *, timeout: float) -> int:
            nonlocal cleaned
            assert timeout == 120
            with (self.socket / ".parent-lock").open("rb") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lab.cleanup_owned(self.owner, self.socket, self.output)
            cleaned = True
            return 0

    def fake_start(owner: str, socket: Path, output: Path, _password: str):
        descriptor = os.open(socket / ".parent-lock", os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor, FakeWatcher(owner, socket, output)

    def timed_out_probe(*_args: object, timeout: float, **_kwargs: object):
        calls.append(timeout)
        if interrupted:
            stop.set()
        raise subprocess.TimeoutExpired("docker", timeout)

    def fake_docker(*args: str, timeout: float = 30) -> str:
        return "" if args[0] == "ps" else "unready"

    with (
        patch.object(lab, "default_smoke_output", side_effect=lambda owner: tmp_path / owner),
        patch.object(lab, "start_watcher", side_effect=fake_start),
        patch.object(lab, "wait_for_launch"),
        patch.object(lab.subprocess, "run", side_effect=timed_out_probe),
        patch.object(lab.time, "sleep"),
        patch.object(lab, "docker", side_effect=fake_docker),
    ):
        if interrupted:
            with pytest.raises(lab.LabInterrupted), lab.disposable_postgres(stop.is_set):
                pass
        else:
            with (
                pytest.raises(RuntimeError, match="did not become ready"),
                lab.disposable_postgres(),
            ):
                pass
    assert calls == [5] * (1 if interrupted else 60)
    assert cleaned


def test_blocked_probe_interruption_waits_for_probe_exit_then_cleanup(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    stop = threading.Event()
    cleaned = threading.Event()
    errors: list[BaseException] = []

    class FakeWatcher:
        def __init__(self, owner: str, socket: Path, output: Path) -> None:
            self.owner, self.socket, self.output = owner, socket, output

        def wait(self, *, timeout: float) -> int:
            assert timeout == 120
            lab.cleanup_owned(self.owner, self.socket, self.output)
            cleaned.set()
            return 0

    def fake_start(owner: str, socket: Path, output: Path, _password: str):
        descriptor = os.open(socket / ".parent-lock", os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor, FakeWatcher(owner, socket, output)

    def blocked_probe(*_args: object, timeout: float, **_kwargs: object):
        assert timeout == 5
        entered.set()
        assert release.wait(3)
        raise subprocess.TimeoutExpired("docker", timeout)

    def parent() -> None:
        try:
            with lab.disposable_postgres(stop.is_set):
                pass
        except BaseException as exc:
            errors.append(exc)

    with (
        patch.object(lab, "default_smoke_output", side_effect=lambda owner: tmp_path / owner),
        patch.object(lab, "start_watcher", side_effect=fake_start),
        patch.object(lab, "wait_for_launch"),
        patch.object(lab.subprocess, "run", side_effect=blocked_probe),
        patch.object(lab, "docker", return_value=""),
    ):
        thread = threading.Thread(target=parent)
        thread.start()
        assert entered.wait(3)
        assert not cleaned.is_set()
        stop.set()
        release.set()
        thread.join(timeout=3)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], lab.LabInterrupted)
    assert cleaned.is_set()


def watcher_inputs(tmp_path: Path, owner: str) -> tuple[Path, Path]:
    socket = Path(tempfile.mkdtemp(prefix=f"kv-client-lab-{owner}-", dir="/tmp"))
    (socket / ".kv-client-lab-owner").write_text(owner)
    password = socket / ".db-password"
    password.write_text("synthetic")
    password.chmod(0o600)
    (socket / ".parent-lock").touch(mode=0o600)
    output = tmp_path / "run"
    lab.prepare_output(output, owner, socket)
    return socket, output


@pytest.mark.parametrize(
    "invalid",
    [
        "missing_output",
        "malformed_owner",
        "foreign_path",
        "symlink",
        "wrong_marker",
        "unexpected_mount",
        "malformed_manifest",
        "started_lifecycle",
        "password_symlink",
    ],
)
def test_direct_watcher_refuses_invalid_invocation(tmp_path: Path, invalid: str) -> None:
    owner = "1" * 32
    socket, output = watcher_inputs(tmp_path, owner)
    argument_owner = owner
    argument_socket = socket
    argument_output: Path | None = output
    if invalid == "missing_output":
        argument_output = None
    elif invalid == "malformed_owner":
        argument_owner = "bad-owner"
    elif invalid == "foreign_path":
        argument_socket = tmp_path
    elif invalid == "symlink":
        argument_socket = Path(f"/tmp/kv-client-lab-{owner}-alias123")  # noqa: S108
        argument_socket.symlink_to(socket, target_is_directory=True)
    elif invalid == "wrong_marker":
        (socket / ".kv-client-lab-owner").write_text("foreign")
    elif invalid == "unexpected_mount":
        (output / "environment.json").write_text(
            json.dumps({"lab_owner": owner, "db_socket_dir": str(tmp_path / "unrelated")})
        )
    elif invalid == "malformed_manifest":
        (output / "environment.json").write_text("[]")
    elif invalid == "started_lifecycle":
        state = lab.read_lifecycle(output, owner)
        state["launch"] = "started"
        lab.write_lifecycle(output, owner, state)
    elif invalid == "password_symlink":
        (socket / ".db-password").unlink()
        (socket / ".db-password").symlink_to(output / "environment.json")
    try:
        with (
            patch.object(lab, "docker") as docker_call,
            pytest.raises(RuntimeError),
        ):
            lab.watch_parent(argument_owner, argument_socket, argument_output)
        docker_call.assert_not_called()
    finally:
        if invalid == "symlink":
            argument_socket.unlink()


def test_direct_watcher_refuses_already_claimed_run(tmp_path: Path) -> None:
    owner = "a" * 32
    socket, output = watcher_inputs(tmp_path, owner)
    (socket / ".watcher-claim").touch(mode=0o600)
    with (
        patch.object(lab, "docker") as docker_call,
        pytest.raises(RuntimeError, match="already claimed"),
    ):
        lab.watch_parent(owner, socket, output)
    docker_call.assert_not_called()


def test_smoke_records_recovery_before_docker_startup(tmp_path: Path) -> None:
    def refuse_start(owner: str, socket: Path, output: Path, _password: str):
        state = lab.read_lifecycle(output, owner)
        assert state["launch"] == "pending" and state["cleanup"] == "pending"
        assert state["socket_dir"] == str(socket)
        assert _password not in (output / "environment.json").read_text()
        assert _password not in (output / ".lifecycle-status").read_text()
        raise RuntimeError("synthetic startup failure")

    with (
        patch.object(lab, "check_host", return_value={}),
        patch.object(lab.secrets, "token_urlsafe", return_value="synthetic-lab-password"),
        patch.object(lab, "default_smoke_output", side_effect=lambda owner: tmp_path / owner),
        patch.object(lab, "start_watcher", side_effect=refuse_start),
        patch.object(lab, "docker", return_value=""),
        pytest.raises(RuntimeError, match="synthetic startup failure"),
    ):
        asyncio.run(lab.run("smoke", None))
    outputs = list(tmp_path.iterdir())
    assert len(outputs) == 1
    owner = outputs[0].name
    assert lab.read_lifecycle(outputs[0], owner)["cleanup"] == "complete"


def test_smoke_recovery_root_is_private_and_outside_disposable_socket(tmp_path: Path) -> None:
    owner = "b" * 32
    with patch.object(Path, "home", return_value=tmp_path):
        output = lab.default_smoke_output(owner)
    assert output == tmp_path / ".local/state/knowledge-vault/client-lab" / f"smoke-{owner}"
    assert output.parent.stat().st_mode & 0o777 == 0o700


def test_smoke_uncertain_launch_keeps_recovery_record(tmp_path: Path) -> None:
    def fake_start(owner: str, socket: Path, output: Path, password: str):
        descriptor = os.open(socket / ".parent-lock", os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        secret_file = socket / ".db-password"
        secret_file.write_text(password)
        secret_file.chmod(0o600)

        class FakeWatcher:
            def wait(self, *, timeout: float) -> int:
                assert timeout == 120
                with pytest.raises(RuntimeError, match="did not complete successfully"):
                    lab.watch_parent(owner, socket, output)
                return 1

        return descriptor, FakeWatcher()

    def timeout_docker(*args: str, timeout: float = 30) -> str:
        if args[0] == "run":
            raise subprocess.TimeoutExpired("docker", timeout)
        return ""

    with (
        patch.object(lab, "check_host", return_value={}),
        patch.object(lab, "default_smoke_output", side_effect=lambda owner: tmp_path / owner),
        patch.object(lab, "start_watcher", side_effect=fake_start),
        patch.object(lab, "wait_for_launch", side_effect=lab.LabInterrupted),
        patch.object(lab, "docker", side_effect=timeout_docker),
        pytest.raises(RuntimeError, match="watcher reported incomplete cleanup"),
    ):
        asyncio.run(lab.run("smoke", None))
    outputs = list(tmp_path.iterdir())
    assert len(outputs) == 1
    output = outputs[0]
    owner = output.name
    state = lab.read_lifecycle(output, owner)
    assert not Path(str(state["socket_dir"])).exists()
    assert lab.read_lifecycle(output, owner)["launch"] == "uncertain"
    state["parent"] = [999999999, 1]
    state["watcher"] = [999999998, 1]
    lab.write_lifecycle(output, owner, state)
    with (
        patch.object(lab, "docker", return_value=""),
        pytest.raises(RuntimeError, match="launch outcome is unresolved"),
    ):
        lab.cleanup_interrupted(output)


@pytest.mark.parametrize("phase", ["before", "during", "after"])
def test_launcher_completes_before_cleanup_even_when_parent_exits(
    tmp_path: Path, phase: str
) -> None:
    owner = "f" * 32
    socket, output = watcher_inputs(tmp_path, owner)
    descriptor = os.open(socket / ".parent-lock", os.O_RDWR)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    launch_entered = threading.Event()
    allow_creation = threading.Event()
    created = threading.Event()
    cleaned = threading.Event()

    def mark_cleaned(_owner: str, _socket: Path, _output: Path | None) -> None:
        assert created.is_set()
        cleaned.set()

    def delayed_docker(*args: str, timeout: float = 30) -> str:
        assert args[:2] == ("run", "-d") and timeout == 75
        launch_entered.set()
        assert allow_creation.wait(3)
        created.set()
        return "synthetic-container-id"

    with (
        patch.object(lab, "cleanup_owned", side_effect=mark_cleaned),
        patch.object(lab, "docker", side_effect=delayed_docker),
    ):
        if phase == "before":
            os.close(descriptor)
        watcher = threading.Thread(target=lab.watch_parent, args=(owner, socket, output))
        watcher.start()
        assert launch_entered.wait(2)
        if phase == "during":
            os.close(descriptor)
            assert not cleaned.is_set()
        allow_creation.set()
        assert created.wait(2)
        if phase == "after":
            os.close(descriptor)
        watcher.join(timeout=3)
    assert not watcher.is_alive()
    assert cleaned.is_set()
    assert (socket / ".launch-status").read_text() == "started"


def test_failed_launcher_reports_incomplete_cleanup(tmp_path: Path) -> None:
    owner = "a" * 32
    socket, output = watcher_inputs(tmp_path, owner)
    with (
        patch.object(lab, "docker", side_effect=OSError("synthetic Docker failure")),
        patch.object(lab, "cleanup_owned"),
        pytest.raises(RuntimeError, match="did not complete successfully"),
    ):
        lab.watch_parent(owner, socket, output)
    assert (socket / ".launch-status").read_text() == "failed"


def test_manual_cleanup_waits_for_launch_and_parent_lock(tmp_path: Path) -> None:
    owner = "2" * 32
    socket_dir = Path(tempfile.mkdtemp(prefix=f"kv-client-lab-{owner}-", dir="/tmp"))
    (socket_dir / ".kv-client-lab-owner").write_text(owner)
    descriptor = os.open(socket_dir / ".parent-lock", os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    output = tmp_path / "run"
    lab.prepare_output(output, owner, socket_dir)
    state = lab.read_lifecycle(output, owner)
    state["parent"] = [999999999, 1]
    state["watcher"] = [999999998, 1]
    lab.write_lifecycle(output, owner, state)
    (output / "lab-token").write_text("synthetic")
    with patch.object(lab, "docker", return_value=""):
        with pytest.raises(RuntimeError, match="launch outcome is unresolved"):
            lab.cleanup_interrupted(output)
        (socket_dir / ".launch-status").write_text("started")
        state["launch"] = "started"
        lab.write_lifecycle(output, owner, state)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        with pytest.raises(RuntimeError, match="lab parent or watcher is active"):
            lab.cleanup_interrupted(output)
        os.close(descriptor)
        lab.cleanup_interrupted(output)
        lab.cleanup_interrupted(output)
    assert not socket_dir.exists()
    assert not (output / "lab-token").exists()


def test_manual_recovery_of_uncertain_launch_requires_explicit_confirmation(tmp_path: Path) -> None:
    owner = "3" * 32
    socket_dir = Path(tempfile.mkdtemp(prefix=f"kv-client-lab-{owner}-", dir="/tmp"))
    (socket_dir / ".kv-client-lab-owner").write_text(owner)
    (socket_dir / ".parent-lock").touch()
    output = tmp_path / "run"
    lab.prepare_output(output, owner, socket_dir)
    state = lab.read_lifecycle(output, owner)
    state.update(launch="uncertain", parent=[999999999, 1], watcher=[999999998, 1])
    lab.write_lifecycle(output, owner, state)
    with patch.object(lab, "docker", return_value=""):
        with pytest.raises(RuntimeError, match="launch outcome is unresolved"):
            lab.cleanup_interrupted(output)
        lab.cleanup_interrupted(output, confirmed_settled=True)
    assert not socket_dir.exists()


def test_watcher_uncertain_launch_after_socket_removal_requires_confirmation(
    tmp_path: Path,
) -> None:
    owner = "4" * 32
    socket_dir, output = watcher_inputs(tmp_path, owner)
    state = lab.read_lifecycle(output, owner)
    state["parent"] = [999999999, 1]
    lab.write_lifecycle(output, owner, state)

    def fake_docker(*args: str, timeout: float = 30) -> str:
        if args[0] == "run":
            raise subprocess.TimeoutExpired("docker", timeout)
        return ""

    with (
        patch.object(lab, "docker", side_effect=fake_docker),
        patch.object(lab, "process_identity", return_value=[999999998, 1]),
        pytest.raises(RuntimeError, match="did not complete successfully"),
    ):
        lab.watch_parent(owner, socket_dir, output)
    assert not socket_dir.exists()
    state = lab.read_lifecycle(output, owner)
    assert (state["launch"], state["cleanup"]) == ("uncertain", "incomplete")
    with patch.object(lab, "docker", return_value=""):
        with pytest.raises(RuntimeError, match="launch outcome is unresolved"):
            lab.cleanup_interrupted(output)
        lab.cleanup_interrupted(output, confirmed_settled=True)
        lab.cleanup_interrupted(output)
    assert lab.read_lifecycle(output, owner)["settled_confirmed"] is True


def test_failed_before_docker_has_safe_completed_cleanup(tmp_path: Path) -> None:
    owner = "8" * 32
    socket_dir, output = watcher_inputs(tmp_path, owner)
    state = lab.read_lifecycle(output, owner)
    state["parent"] = [999999999, 1]
    lab.write_lifecycle(output, owner, state)
    original_read = Path.read_text

    def failed_password_read(path: Path, *args: object, **kwargs: object) -> str:
        if path.name == ".db-password":
            raise OSError("synthetic prelaunch failure")
        return original_read(path, *args, **kwargs)

    with (
        patch.object(lab, "docker", return_value="") as docker_call,
        patch.object(lab, "process_identity", return_value=[999999998, 1]),
        patch.object(Path, "read_text", failed_password_read),
    ):
        lab.watch_parent(owner, socket_dir, output)
    assert not socket_dir.exists()
    assert not any(call.args[0] == "run" for call in docker_call.call_args_list)
    state = lab.read_lifecycle(output, owner)
    assert (state["launch"], state["cleanup"]) == ("failed_safe", "complete")
    with patch.object(lab, "docker", return_value=""):
        lab.cleanup_interrupted(output)


def test_contradictory_lifecycle_refuses_cleanup(tmp_path: Path) -> None:
    owner = "9" * 32
    output = tmp_path / "run"
    lab.prepare_output(output, owner, Path(f"/tmp/kv-client-lab-{owner}-missing"))  # noqa: S108
    state = lab.read_lifecycle(output, owner)
    state.update(launch="uncertain", cleanup="complete", parent=[999999999, 1])
    lab.write_lifecycle(output, owner, state)
    with (
        patch.object(lab, "docker", return_value=""),
        pytest.raises(RuntimeError, match="contradictory lab lifecycle status"),
    ):
        lab.cleanup_interrupted(output, confirmed_settled=True)


@pytest.mark.parametrize("status", [None, "garbage", '{"launch": "started"}'])
def test_cleanup_fails_closed_on_missing_or_corrupt_lifecycle(
    tmp_path: Path, status: str | None
) -> None:
    owner = "5" * 32
    output = tmp_path / "run"
    lab.prepare_output(output, owner, Path(f"/tmp/kv-client-lab-{owner}-missing"))  # noqa: S108
    path = output / ".lifecycle-status"
    if status is None:
        path.unlink()
    else:
        path.write_text(status)
    with (
        patch.object(lab, "docker", return_value=""),
        pytest.raises(RuntimeError, match="lifecycle status"),
    ):
        lab.cleanup_interrupted(output, confirmed_settled=True)


@pytest.mark.parametrize("active", ["parent", "watcher"])
def test_missing_socket_cleanup_refuses_active_process(tmp_path: Path, active: str) -> None:
    owner = "6" * 32
    output = tmp_path / "run"
    lab.prepare_output(output, owner, Path(f"/tmp/kv-client-lab-{owner}-missing"))  # noqa: S108
    state = lab.read_lifecycle(output, owner)
    state.update(
        launch="uncertain", cleanup="incomplete", parent=[999999999, 1], watcher=[999999998, 1]
    )
    state[active] = lab.process_identity(os.getpid())
    lab.write_lifecycle(output, owner, state)
    with (
        patch.object(lab, "docker", return_value=""),
        pytest.raises(RuntimeError, match="lab parent or watcher is active"),
    ):
        lab.cleanup_interrupted(output, confirmed_settled=True)


def test_foreign_container_blocks_missing_socket_recovery(tmp_path: Path) -> None:
    owner = "7" * 32
    output = tmp_path / "run"
    lab.prepare_output(output, owner, Path(f"/tmp/kv-client-lab-{owner}-missing"))  # noqa: S108
    state = lab.read_lifecycle(output, owner)
    state.update(
        launch="uncertain", cleanup="incomplete", parent=[999999999, 1], watcher=[999999998, 1]
    )
    lab.write_lifecycle(output, owner, state)
    calls: list[tuple[str, ...]] = []

    def foreign_docker(*args: str, timeout: float = 30) -> str:
        calls.append(args)
        return f"kv-client-lab-db-{owner}"

    with (
        patch.object(lab, "docker", side_effect=foreign_docker),
        pytest.raises(RuntimeError, match="without expected owner label"),
    ):
        lab.cleanup_interrupted(output, confirmed_settled=True)
    assert not any(call[0] == "rm" for call in calls)


def test_generated_credentials_have_no_admin_scope_or_secret_in_settings_repr() -> None:
    settings, token = lab.credentials("postgresql+psycopg://lab:generated@127.0.0.1:54321/lab")
    assert token not in repr(settings)
    assert "knowledge:admin" not in settings.bootstrap_tokens
    assert settings.embeddings_enabled is False


def test_manifest_does_not_include_url_or_token(tmp_path: Path) -> None:
    output = tmp_path / "run"
    token = "synthetic-evaluation-token"
    owner = "e" * 32
    socket_dir = Path(f"/tmp/kv-client-lab-{owner}-missing")  # noqa: S108
    lab.prepare_output(output, owner, socket_dir)
    manifest = {
        "lab_owner": owner,
        "endpoint": "http://127.0.0.1:12345",
        "embedding_revision": None,
    }
    lab.write_operator_files(output, token, manifest)
    assert token not in (output / "environment.json").read_text()
    assert "database_url" not in (output / "environment.json").read_text()
    assert (output / "lab-token").stat().st_mode & 0o777 == 0o600


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
    output = tmp_path / "run"
    lab.prepare_output(output, owner, socket_dir)
    state = lab.read_lifecycle(output, owner)
    state.update(
        launch="started", cleanup="complete", parent=[999999999, 1], watcher=[999999998, 1]
    )
    lab.write_lifecycle(output, owner, state)
    token = output / "lab-token"
    token.write_text("synthetic")
    calls: list[tuple[str, ...]] = []

    def fake_docker(*args: str) -> str:
        calls.append(args)
        return ""

    with (
        patch.object(lab, "docker", fake_docker),
        patch.object(lab, "owned_resource", return_value=True),
    ):
        lab.cleanup_interrupted(output)
    assert ("rm", "-f", "-v", f"kv-client-lab-db-{owner}") in calls
    assert not token.exists()


def test_foreign_container_is_never_removed() -> None:
    owner = "c" * 32
    container = f"kv-client-lab-db-{owner}"
    calls: list[tuple[str, ...]] = []

    def fake_docker(*args: str) -> str:
        calls.append(args)
        return container

    with (
        patch.object(lab, "owned_resource", return_value=False),
        patch.object(lab, "docker", fake_docker),
        pytest.raises(RuntimeError, match="without expected owner label"),
    ):
        lab.remove_owned_container(container, owner)
    assert not any(call[0] == "rm" for call in calls)


def test_cleanup_is_idempotent(tmp_path: Path) -> None:
    owner = "d" * 32
    socket_dir = Path(f"/tmp/kv-client-lab-{owner}-missing")  # noqa: S108
    output = tmp_path / "run"
    lab.prepare_output(output, owner, socket_dir)
    state = lab.read_lifecycle(output, owner)
    state.update(
        launch="started", cleanup="complete", parent=[999999999, 1], watcher=[999999998, 1]
    )
    lab.write_lifecycle(output, owner, state)
    (output / "lab-token").write_text("synthetic")
    with (
        patch.object(lab, "owned_resource", return_value=False),
        patch.object(lab, "docker", return_value=""),
    ):
        lab.cleanup_interrupted(output)
        lab.cleanup_interrupted(output)
    assert not (output / "lab-token").exists()


def test_sigint_and_sigterm_request_stop_and_restore_handlers() -> None:
    stop = asyncio.Event()
    old_int = signal.getsignal(signal.SIGINT)
    old_term = signal.getsignal(signal.SIGTERM)
    with lab.operator_signals(stop):
        handler = signal.getsignal(signal.SIGINT)
        assert callable(handler)
        handler(signal.SIGINT, None)
        assert stop.is_set()
        stop.clear()
        term_handler = signal.getsignal(signal.SIGTERM)
        assert callable(term_handler)
        term_handler(signal.SIGTERM, None)
        assert stop.is_set()
    assert signal.getsignal(signal.SIGINT) == old_int
    assert signal.getsignal(signal.SIGTERM) == old_term


@pytest.mark.parametrize("no_proxy", ["", "example.invalid", "localhost"])
async def test_local_http_client_ignores_all_inherited_proxies(
    monkeypatch: pytest.MonkeyPatch, no_proxy: str
) -> None:
    target_requests: list[bytes] = []
    proxy_requests: list[bytes] = []

    async def receive(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter, sink: list[bytes]
    ) -> None:
        headers = await reader.readuntil(b"\r\n\r\n")
        length = next(
            (
                int(line.split(b":", 1)[1].strip())
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            ),
            0,
        )
        sink.append(headers + await reader.readexactly(length))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def target(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await receive(reader, writer, target_requests)

    async def proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await receive(reader, writer, proxy_requests)

    target_server = await asyncio.start_server(target, "127.0.0.1", 0)
    proxy_server = await asyncio.start_server(proxy, "127.0.0.1", 0)
    try:
        target_port = target_server.sockets[0].getsockname()[1]
        proxy_port = proxy_server.sockets[0].getsockname()[1]
        for key in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ):
            monkeypatch.setenv(key, f"http://127.0.0.1:{proxy_port}")
        monkeypatch.setenv("NO_PROXY", no_proxy)
        monkeypatch.setenv("no_proxy", no_proxy)
        token = "synthetic-evaluation-token"
        payload = b"synthetic-evaluation-payload"
        url = lab.local_mcp_url(f"http://127.0.0.1:{target_port}")
        async with lab.local_http_client(token=token) as client:
            assert client.trust_env is False
            response = await client.post(url, content=payload)
        assert response.status_code == 200
        assert len(target_requests) == 1
        assert token.encode() in target_requests[0]
        assert payload in target_requests[0]
        assert proxy_requests == []
    finally:
        target_server.close()
        proxy_server.close()
        await target_server.wait_closed()
        await proxy_server.wait_closed()


@pytest.mark.parametrize(
    "base", ["https://127.0.0.1:1234", "http://example.org:1234", "http://127.0.0.1:1234/else"]
)
def test_local_mcp_url_refuses_nonlocal_transport(base: str) -> None:
    with pytest.raises(RuntimeError, match=r"127\.0\.0\.1"):
        lab.local_mcp_url(base)
