import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.feasibility_workspace import Workspace, WorkspaceError, recover


def test_owner_cleanup_and_foreign_refusal(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path)
    with pytest.raises(WorkspaceError, match="owner mismatch"):
        recover(workspace.root, "0" * 32)
    with pytest.raises(WorkspaceError, match="active"):
        recover(workspace.root, workspace.owner)
    workspace.cleanup()
    assert not workspace.root.exists()


def test_recovery_after_owner_exits(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path)
    os.close(workspace._descriptor)
    recover(workspace.root, workspace.owner)
    assert not workspace.root.exists()


def test_recovery_refuses_symlink(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path)
    link = tmp_path / "kv-feasibility-link"
    link.symlink_to(workspace.root, target_is_directory=True)
    with pytest.raises(WorkspaceError, match="invalid workspace"):
        recover(link, workspace.owner)
    workspace.cleanup()


def test_recovery_after_abrupt_owner_termination(tmp_path: Path) -> None:
    code = (
        "import sys,time; from pathlib import Path; "
        "from scripts.feasibility_workspace import Workspace; "
        "w=Workspace.create(Path(sys.argv[1])); "
        "print(w.root, w.owner, flush=True); time.sleep(30)"
    )
    process = subprocess.Popen(  # noqa: S603  # Fixed interpreter and test-only code.
        [sys.executable, "-c", code, str(tmp_path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        root, owner = process.stdout.readline().split()
        with pytest.raises(WorkspaceError, match="active"):
            recover(Path(root), owner)
        process.kill()
        process.wait(timeout=5)
        recover(Path(root), owner)
        assert not Path(root).exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()


def test_unsettled_child_refuses_automatic_recovery(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path)
    workspace.mark_launched()
    with pytest.raises(WorkspaceError, match="child may still be active"):
        workspace.cleanup()
    workspace.mark_settled()
    workspace.cleanup()
    assert not workspace.root.exists()


def test_abandoned_unsettled_workspace_is_preserved(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path)
    workspace.mark_launched()
    os.close(workspace._descriptor)
    with pytest.raises(WorkspaceError, match="manual recovery"):
        recover(workspace.root, workspace.owner)
    assert workspace.root.exists()
