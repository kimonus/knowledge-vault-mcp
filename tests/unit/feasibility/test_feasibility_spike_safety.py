from pathlib import Path

import pytest
from scripts import feasibility_spike
from scripts.feasibility_workspace import Workspace


@pytest.mark.parametrize("link_path", ["opt", "opt/python", "opt/site", "opt/repo"])
def test_mount_directories_refuse_extracted_symlink_components(
    tmp_path: Path, link_path: str
) -> None:
    image_root = tmp_path / "rootfs"
    image_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    symlink = image_root / link_path
    symlink.parent.mkdir(parents=True, exist_ok=True)
    symlink.symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        feasibility_spike.prepare_mount_directories(image_root)

    assert list(outside.iterdir()) == []


def test_staging_namespace_has_only_rootfs_as_writable_host_bind(tmp_path: Path) -> None:
    layers = tmp_path / "layers"
    rootfs = tmp_path / "rootfs"
    command = feasibility_spike.staging_command(layers, rootfs)
    writable_binds = [
        command[index + 1 : index + 3]
        for index, argument in enumerate(command)
        if argument == "--bind"
    ]

    assert writable_binds == [[str(rootfs), "/rootfs"]]
    assert command[command.index("--tmpfs") + 1] == "/tmp"  # noqa: S108
    assert "--ro-bind" in command


def test_post_staging_setup_failure_cleans_settled_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace.create(tmp_path)
    image_root = workspace.root / "rootfs"
    image_root.mkdir()
    original = feasibility_spike.prepare_mount_directories

    def fail_after_partial_setup(root: Path) -> None:
        original(root)
        raise OSError("injected host preparation failure")

    monkeypatch.setattr(feasibility_spike, "prepare_mount_directories", fail_after_partial_setup)
    with pytest.raises(OSError, match="injected host preparation failure"):
        feasibility_spike.prepare_run_commands(
            workspace,
            image_root,
            tmp_path,
            tmp_path,
            "synthetic-revision",
            False,
        )

    assert not workspace.root.exists()


def test_post_staging_setup_failure_preserves_unsettled_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace.create(tmp_path)
    image_root = workspace.root / "rootfs"
    image_root.mkdir()
    workspace.mark_launched()

    def fail(root: Path) -> None:
        raise OSError("injected host preparation failure")

    monkeypatch.setattr(feasibility_spike, "prepare_mount_directories", fail)
    with pytest.raises(OSError, match="injected host preparation failure"):
        feasibility_spike.prepare_run_commands(
            workspace,
            image_root,
            tmp_path,
            tmp_path,
            "synthetic-revision",
            False,
        )

    assert workspace.root.exists()
    assert (workspace.root / "child-unsettled").exists()
    workspace.mark_settled()
    workspace.cleanup()
