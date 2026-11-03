"""Safety checks for the disposable client evaluation lab."""

import asyncio
import fcntl
import importlib.util
import json
import os
import signal
import tempfile
import threading
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
        return ""

    with (
        patch.object(lab, "docker", fake_docker),
        patch.object(lab, "start_watcher", side_effect=RuntimeError("interrupted startup")),
        pytest.raises(RuntimeError, match="interrupted startup"),
        lab.disposable_postgres(),
    ):
        pass
    assert not any(call[0] == "rm" for call in calls)


@pytest.mark.parametrize("cancelled", [False, True])
def test_disposable_postgres_waits_for_launcher_cleanup(cancelled: bool) -> None:
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


def test_startup_signal_waits_for_launcher_cleanup() -> None:
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
        patch.object(lab, "start_watcher", side_effect=fake_start),
        patch.object(lab, "wait_for_launch", side_effect=mark_started),
        pytest.raises(lab.LabInterrupted),
        lab.disposable_postgres(lambda: started),
    ):
        pass
    assert waited


@pytest.mark.parametrize("phase", ["before", "during", "after"])
def test_launcher_completes_before_cleanup_even_when_parent_exits(
    tmp_path: Path, phase: str
) -> None:
    owner = "f" * 32
    (tmp_path / ".kv-client-lab-owner").write_text(owner)
    (tmp_path / ".db-password").write_text("synthetic")
    descriptor = os.open(tmp_path / ".parent-lock", os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
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
        watcher = threading.Thread(target=lab.watch_parent, args=(owner, tmp_path, None))
        watcher.start()
        assert launch_entered.wait(2)
        if phase == "during":
            os.close(descriptor)
            # The old sleep(1) would have checked for the container by now.
            assert not cleaned.wait(1.1)
        allow_creation.set()
        assert created.wait(2)
        if phase == "after":
            os.close(descriptor)
        watcher.join(timeout=3)
    assert not watcher.is_alive()
    assert cleaned.is_set()
    assert (tmp_path / ".launch-status").read_text() == "started"


def test_failed_launcher_reports_incomplete_cleanup(tmp_path: Path) -> None:
    owner = "a" * 32
    (tmp_path / ".kv-client-lab-owner").write_text(owner)
    (tmp_path / ".db-password").write_text("synthetic")
    (tmp_path / ".parent-lock").touch()
    with (
        patch.object(lab, "docker", side_effect=OSError("synthetic Docker failure")),
        patch.object(lab, "cleanup_owned"),
        pytest.raises(RuntimeError, match="did not complete successfully"),
    ):
        lab.watch_parent(owner, tmp_path, None)
    assert (tmp_path / ".launch-status").read_text() == "failed"


def test_manual_cleanup_waits_for_launch_and_parent_lock(tmp_path: Path) -> None:
    owner = "2" * 32
    socket_dir = Path(tempfile.mkdtemp(prefix=f"kv-client-lab-{owner}-", dir="/tmp"))
    (socket_dir / ".kv-client-lab-owner").write_text(owner)
    descriptor = os.open(socket_dir / ".parent-lock", os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    output = tmp_path / "run"
    lab.prepare_output(output, owner, socket_dir)
    (output / "lab-token").write_text("synthetic")
    with patch.object(lab, "docker", return_value=""):
        with pytest.raises(RuntimeError, match="launch outcome is unresolved"):
            lab.cleanup_interrupted(output)
        (socket_dir / ".launch-status").write_text("started")
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
    with patch.object(lab, "docker", return_value=""):
        with pytest.raises(RuntimeError, match="launch outcome is unresolved"):
            lab.cleanup_interrupted(output)
        lab.cleanup_interrupted(output, confirmed_settled=True)
    assert not socket_dir.exists()


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
    (tmp_path / "environment.json").write_text(
        json.dumps({"lab_owner": owner, "db_socket_dir": str(socket_dir)})
    )
    (tmp_path / ".kv-client-lab-owner").write_text(owner)
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
    (tmp_path / "environment.json").write_text(
        json.dumps({"lab_owner": owner, "db_socket_dir": str(socket_dir)})
    )
    (tmp_path / ".kv-client-lab-owner").write_text(owner)
    (tmp_path / "lab-token").write_text("synthetic")
    with (
        patch.object(lab, "owned_resource", return_value=False),
        patch.object(lab, "docker", return_value=""),
    ):
        lab.cleanup_interrupted(tmp_path)
        lab.cleanup_interrupted(tmp_path)
    assert not (tmp_path / "lab-token").exists()


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
