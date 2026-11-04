import sys
from pathlib import Path

import pytest
from scripts.feasibility_lifecycle import RunError, run_pair
from scripts.feasibility_workspace import Workspace


def test_pair_success_reaps_and_cleans(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path)
    server = [sys.executable, "-c", "print('SYNTHETIC_GATEWAY_READY', flush=True)"]
    client = [sys.executable, "-c", "pass"]
    run_pair(workspace, server, client, timeout=5)
    assert not workspace.root.exists()


def test_pair_failure_reaps_and_cleans(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path)
    server = [sys.executable, "-c", "print('SYNTHETIC_GATEWAY_READY', flush=True)"]
    client = [sys.executable, "-c", "raise SystemExit(1)"]
    with pytest.raises(RunError, match="client failed"):
        run_pair(workspace, server, client, timeout=5)
    assert not workspace.root.exists()


def test_pair_readiness_timeout_reaps_and_cleans(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path)
    server = [sys.executable, "-c", "import time; time.sleep(10)"]
    client = [sys.executable, "-c", "pass"]
    with pytest.raises(RunError, match="readiness timeout"):
        run_pair(workspace, server, client, timeout=0.05)
    assert not workspace.root.exists()


@pytest.mark.parametrize("timeout", [0, -1])
def test_nonpositive_timeout_is_rejected_before_launch(tmp_path: Path, timeout: float) -> None:
    workspace = Workspace.create(tmp_path)
    with pytest.raises(RunError, match="timeout must be positive"):
        run_pair(workspace, [], [], timeout=timeout)
    assert not (workspace.root / "child-unsettled").exists()
    workspace.cleanup()


@pytest.mark.parametrize(
    ("server_code", "message"),
    [
        ("pass", "exited before readiness"),
        ("print('NOT_READY', flush=True)", "did not report readiness"),
        ("print('x' * 140, flush=True)", "did not report readiness"),
    ],
)
def test_invalid_server_readiness_reaps_and_cleans(
    tmp_path: Path, server_code: str, message: str
) -> None:
    workspace = Workspace.create(tmp_path)
    server = [sys.executable, "-c", server_code]
    with pytest.raises(RunError, match=message):
        run_pair(workspace, server, [sys.executable, "-c", "pass"], timeout=2)
    assert not workspace.root.exists()


def test_server_failure_is_reported_after_successful_client(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path)
    server = [
        sys.executable,
        "-c",
        "print('SYNTHETIC_GATEWAY_READY', flush=True); raise SystemExit(3)",
    ]
    with pytest.raises(RunError, match="server failed"):
        run_pair(workspace, server, [sys.executable, "-c", "pass"], timeout=2)
    assert not workspace.root.exists()


def test_client_timeout_and_success_callback(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path)
    server = [
        sys.executable,
        "-c",
        "import time; print('SYNTHETIC_GATEWAY_READY', flush=True); time.sleep(10)",
    ]
    with pytest.raises(RunError, match="run timed out"):
        run_pair(workspace, server, [sys.executable, "-c", "pass"], timeout=0.5)
    assert not workspace.root.exists()

    workspace = Workspace.create(tmp_path)
    marker = tmp_path / "success-called"
    server = [sys.executable, "-c", "print('SYNTHETIC_GATEWAY_READY', flush=True)"]
    run_pair(
        workspace,
        server,
        [sys.executable, "-c", "pass"],
        timeout=2,
        on_success=lambda: marker.write_text("ok"),
    )
    assert marker.read_text() == "ok"
    assert not workspace.root.exists()
