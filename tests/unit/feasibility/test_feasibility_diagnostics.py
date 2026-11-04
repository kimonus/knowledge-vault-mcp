"""Offline startup failures use fake children only; no database, image or network."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import feasibility_diagnostics as diagnosis
from scripts.feasibility_lifecycle import RunError, run_pair
from scripts.feasibility_spike import prepare_run_commands
from scripts.feasibility_support import startup
from scripts.feasibility_workspace import Workspace

SECRET = "Bearer fake-authorization-secret postgresql://fake:secret@private/db"


@pytest.fixture
def diagnostic(tmp_path: Path):
    directory = tmp_path / "diagnostics"
    directory.mkdir(mode=0o700)
    value = diagnosis.StartupDiagnostics(directory)
    yield value
    value.close()


@pytest.mark.parametrize("stage", diagnosis.STAGES)
def test_each_stage_has_finite_evidence(diagnostic, monkeypatch, stage):
    monkeypatch.setattr(diagnosis, "SANDBOX_PATH", str(diagnostic.path))
    diagnosis.write_record(stage)
    assert diagnostic.read()["stage_entered"] == stage
    assert diagnostic.read()["category"] == "entered"
    diagnosis.write_record(stage, "completed")
    assert diagnostic.read()["last_completed"] == stage
    assert diagnostic.path.stat().st_size <= diagnosis.MAX_BYTES


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xfe",
        b"x" * 10000,
        b"v1 initdb sandbox_launch entered 0",
        b"v1 initdb sandbox_launch entered 0\nextra",
        b"v2 initdb sandbox_launch entered 0\n",
        b"v1 unknown sandbox_launch entered 0\n",
        b"v1 initdb secret entered 0\n",
        b"v1 initdb sandbox_launch entered 256\n",
        b"v1 initdb sandbox_launch entered -1\n",
        b"v1 initdb sandbox_launch entered 00\n",
        b"v1 initdb initdb entered 0\n",
        b"v1 initdb sandbox_launch \x1b[31m 0\n",
        SECRET.encode(),
        b"v1 initdb sandbox_launch entered 0\n" + SECRET.encode(),
        b"v1 initdb sandbox_launch " + SECRET.encode() + b" 0\n",
    ],
    ids=[f"hostile-{n}" for n in range(15)],
)
def test_hostile_records_never_escape(diagnostic, data, capfd):
    diagnostic.path.write_bytes(data)
    report = diagnostic.read()
    assert report["diagnosis"] == "invalid"
    public = json.dumps(report)
    assert SECRET not in public and "private/db" not in public and "\x1b" not in public
    assert len(public) < 256
    assert capfd.readouterr() == ("", "")


@pytest.mark.parametrize("replacement", ["symlink", "file", "fifo", "missing", "mode", "hardlink"])
def test_file_swaps_and_permissions_fail_closed(diagnostic, tmp_path, replacement):
    if replacement == "mode":
        diagnostic.path.chmod(0o644)
    elif replacement == "hardlink":
        os.link(diagnostic.path, tmp_path / "alias")
    else:
        diagnostic.path.unlink()
        if replacement == "symlink":
            diagnostic.path.symlink_to(tmp_path / "secret")
        elif replacement == "file":
            diagnostic.path.write_text(SECRET)
        elif replacement == "fifo":
            os.mkfifo(diagnostic.path)
    assert diagnostic.read()["diagnosis"] == "invalid"


def test_empty_and_directory_swap(diagnostic, tmp_path):
    assert diagnostic.read()["diagnosis"] == "missing"
    old = tmp_path / "original"
    diagnostic.directory.rename(old)
    diagnostic.directory.mkdir(mode=0o700)
    diagnostic.path.write_text(SECRET)
    # Reads stay on the pinned directory, never the replacement path.
    assert diagnostic.read()["diagnosis"] == "missing"


def test_snapshot_overwrites_and_writer_refuses_symlink(diagnostic, tmp_path, monkeypatch):
    monkeypatch.setattr(diagnosis, "SANDBOX_PATH", str(diagnostic.path))
    for _ in range(1000):
        diagnosis.write_record("initdb")
    assert diagnostic.path.stat().st_size < diagnosis.MAX_BYTES
    diagnostic.path.unlink()
    target = tmp_path / "untouched"
    target.write_text("untouched")
    diagnostic.path.symlink_to(target)
    with pytest.raises(OSError):
        diagnosis.write_record("initdb")
    assert target.read_text() == "untouched"


def test_rejects_insecure_directory_before_file_creation(tmp_path):
    directory = tmp_path / "unsafe"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o755
    with pytest.raises(ValueError, match="invalid diagnosis directory"):
        diagnosis.StartupDiagnostics(directory)
    assert not (directory / "startup.status").exists()


@pytest.mark.parametrize("mask", [0o077, 0o022], ids=["umask-077", "umask-022"])
def test_directory_permissions_under_isolated_umask(tmp_path, mask):
    code = """
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from scripts.feasibility_diagnostics import StartupDiagnostics

mask = int(sys.argv[2])
os.umask(mask)
with tempfile.TemporaryDirectory(dir=sys.argv[1]) as parent_name:
    parent = Path(parent_name)
    assert stat.S_IMODE(parent.stat().st_mode) == 0o700
    assert parent.stat().st_uid == os.getuid()
    unsafe = parent / 'unsafe'
    unsafe.mkdir(mode=0o755)
    initial = stat.S_IMODE(unsafe.stat().st_mode)
    assert initial == (0o755 & ~mask)
    unsafe.chmod(0o755)
    assert stat.S_IMODE(unsafe.stat().st_mode) == 0o755
    assert unsafe.stat().st_uid == os.getuid()
    try:
        value = StartupDiagnostics(unsafe)
    except ValueError as exc:
        assert str(exc) == 'invalid diagnosis directory'
    else:
        value.close()
        raise AssertionError('insecure directory accepted')
    assert not (unsafe / 'startup.status').exists()
    secure = parent / 'secure'
    secure.mkdir(mode=0o700)
    assert stat.S_IMODE(secure.stat().st_mode) == 0o700
    assert secure.stat().st_uid == os.getuid()
    value = StartupDiagnostics(secure)
    try:
        assert stat.S_IMODE(value.path.stat().st_mode) == 0o600
        assert value.path.stat().st_uid == os.getuid()
    finally:
        value.close()
    print(json.dumps({'umask': oct(mask), 'initial': oct(initial),
                      'rejected': '0o755', 'accepted': '0o700', 'file': '0o600'}))
"""
    result = subprocess.run(  # noqa: S603  # Fixed offline code; pytest-owned path and mask.
        [sys.executable, "-c", code, str(tmp_path), str(mask)],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert not result.stderr
    assert json.loads(result.stdout) == {
        "umask": oct(mask),
        "initial": oct(0o755 & ~mask),
        "rejected": "0o755",
        "accepted": "0o700",
        "file": "0o600",
    }


def test_changed_during_read_is_not_published(diagnostic, monkeypatch):
    diagnostic.mark("initdb")
    original = os.pread

    def mutate(*args):
        data = original(*args)
        diagnostic.path.write_bytes(b"corrupt")
        return data

    monkeypatch.setattr(diagnosis.os, "pread", mutate)
    assert diagnostic.read()["diagnosis"] == "invalid"


@pytest.mark.parametrize("kind", [ImportError, OSError, RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize("stage", diagnosis.STAGES[:-1])
def test_python_failure_classification_is_sanitized(diagnostic, monkeypatch, capfd, kind, stage):
    monkeypatch.setattr(diagnosis, "SANDBOX_PATH", str(diagnostic.path))
    monkeypatch.setattr(startup, "SANDBOX_PATH", str(diagnostic.path))
    diagnosis.write_record(stage)

    def fail(*args, **kwargs):
        raise kind(SECRET)

    monkeypatch.setattr(startup.runpy, "run_path", fail)
    with pytest.raises(SystemExit) as error:
        startup.main()
    assert error.value.code == 1
    report = diagnostic.read()
    assert report["stage_entered"] == stage
    assert report["category"] in ("import_error", "os_error", "python_error")
    assert SECRET not in json.dumps(report)
    assert capfd.readouterr() == ("", "")


@pytest.mark.parametrize(
    ("code", "expected", "status", "term_signal"),
    [
        ("raise SystemExit(7)", "premature_eof", 7, None),
        ("print('BAD\\x1bLINE', flush=True)", "readiness_error", None, None),
        ("print('x' * 10000, flush=True)", "readiness_error", None, None),
        ("import time; time.sleep(10)", "readiness_timeout", None, None),
        ("import os,signal; os.kill(os.getpid(), signal.SIGTERM)", "premature_eof", None, 15),
    ],
)
def test_failed_readiness_is_bounded_and_no_client_launch(
    tmp_path, diagnostic, code, expected, status, term_signal, capfd
):
    workspace = Workspace.create(tmp_path)
    marker = tmp_path / "client-launched"
    server = [sys.executable, "-c", f"import sys; sys.stderr.write({SECRET!r}); {code}"]
    client = [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]
    with pytest.raises(RunError):
        run_pair(workspace, server, client, timeout=0.3, diagnostics=diagnostic)
    assert diagnostic.report["lifecycle"] == expected
    if status is not None:
        assert diagnostic.report["exit_status"] == status
    if term_signal is not None:
        assert diagnostic.report["signal"] == term_signal
    assert diagnostic.report["stage_entered"] == "sandbox_launch"
    assert diagnostic.report["last_completed"] == "image_staging"
    assert not marker.exists() and not workspace.root.exists()
    assert capfd.readouterr() == ("", "")


def test_success_readiness_unchanged_and_server_only_bind(tmp_path, diagnostic):
    workspace = Workspace.create(tmp_path)
    image = workspace.root / "rootfs"
    image.mkdir()
    server, client, _ = prepare_run_commands(workspace, image, tmp_path, tmp_path, "rev", False)
    assert "/tmp/startup.status" in server  # noqa: S108
    assert not any("diagnostic" in p or "startup.status" in p for p in client)
    for command in (server, client):
        assert all(flag in command for flag in ("--unshare-all", "--die-with-parent", "--clearenv"))
    run_pair(
        workspace,
        [sys.executable, "-c", "print('SYNTHETIC_GATEWAY_READY', flush=True)"],
        [sys.executable, "-c", "pass"],
        timeout=3,
        diagnostics=diagnostic,
    )
    assert diagnostic.report["lifecycle"] == "success"
    assert diagnostic.report["exit_status"] == 0
    assert not workspace.root.exists()


def test_missing_child_evidence_and_run_timeout(tmp_path, diagnostic):
    workspace = Workspace.create(tmp_path)
    with pytest.raises(RunError, match="exited before readiness"):
        run_pair(
            workspace,
            [sys.executable, "-c", f"open({str(diagnostic.path)!r}, 'w').close()"],
            [sys.executable, "-c", "pass"],
            timeout=1,
            diagnostics=diagnostic,
        )
    assert diagnostic.report["category"] == "missing_diagnosis"
    assert diagnostic.report["server_diagnosis"] == "missing"
    workspace = Workspace.create(tmp_path)
    with pytest.raises(RunError, match="run timed out"):
        run_pair(
            workspace,
            [sys.executable, "-c", "print('SYNTHETIC_GATEWAY_READY', flush=True)"],
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout=0.3,
            diagnostics=diagnostic,
        )
    assert diagnostic.report["timed_out"] is True
    assert diagnostic.report["settled"] is True
    assert not workspace.root.exists()


@pytest.mark.parametrize("phase", ["initdb", "pg_start", "alembic", "python_imports"])
def test_shell_failure_phases_without_database(tmp_path, phase):
    """Execute the actual shell control flow with only fixed-path command doubles."""
    support = Path(__file__).resolve().parents[3] / "scripts/feasibility_support"
    record = tmp_path / "startup.status"
    script = (support / "server.sh").read_text()
    script = script.replace("export PATH=/usr/lib/postgresql/17/bin:/usr/bin:/bin", "")
    script = script.replace("/tmp/startup.status", str(record))  # noqa: S108  # Test rewrite.
    fake = tmp_path / "command"
    fake.write_text(
        "#!/bin/sh\n"
        'case "$1" in initdb) step=initdb;; pg_ctl) step=pg_start;; '
        'python) case "$2" in -c) step=alembic;; *) step=python_imports;; esac;; esac\n'
        f'test "$step" != {phase} || exit 23\n'
    )
    fake.chmod(0o700)
    script = script.replace(" initdb --no-locale", f" {fake} initdb --no-locale")
    script = script.replace(" pg_ctl ", f" {fake} pg_ctl ")
    script = script.replace("/opt/python/bin/python3.12", f"{fake} python")
    result = subprocess.run(  # noqa: S603  # Only fake commands, no DB/network.
        ["/bin/sh", "-c", script],
        capture_output=True,
        timeout=3,
    )
    assert result.returncode == 23
    evidence = diagnosis.parse_record(record.read_bytes())
    assert evidence["stage_entered"] == phase
    assert evidence["category"] == ("entered" if phase == "python_imports" else "command_failed")
    assert not result.stdout and not result.stderr


def test_unsettled_child_is_preserved_and_cancellation_reported(tmp_path, diagnostic, monkeypatch):
    workspace = Workspace.create(tmp_path)

    class Child:
        stdout = None

        def poll(self):
            return None

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout):
            raise subprocess.TimeoutExpired("fixed", timeout)

    child = Child()

    # Cancel after Popen has returned and the child has been recorded as owned.
    class Selector:
        def __enter__(self):
            raise KeyboardInterrupt

        def __exit__(self, *args):
            pass

    child.stdout = open(os.devnull, "rb")  # noqa: SIM115  # Closed by lifecycle under test.
    monkeypatch.setattr("scripts.feasibility_lifecycle.subprocess.Popen", lambda *a, **k: child)
    monkeypatch.setattr("scripts.feasibility_lifecycle.selectors.DefaultSelector", Selector)
    with pytest.raises(KeyboardInterrupt):
        run_pair(workspace, ["fixed"], ["fixed"], timeout=1, diagnostics=diagnostic)
    assert diagnostic.report["lifecycle"] == "cancelled"
    assert diagnostic.report["settled"] is False
    assert (workspace.root / "child-unsettled").exists()
    # The fake child is now gone; only test teardown authorizes marker removal.
    workspace.mark_settled()
    workspace.cleanup()
