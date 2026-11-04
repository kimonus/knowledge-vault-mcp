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
