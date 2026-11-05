import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from scripts import feasibility_spike
from scripts.feasibility_workspace import Workspace


def test_library_path_is_mapped_and_not_inherited_by_postgres(tmp_path, monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/untrusted-host-library")
    workspace = Workspace.create(tmp_path)
    image = workspace.root / "rootfs"
    image.mkdir()
    try:
        server, client, _ = feasibility_spike.prepare_run_commands(
            workspace, image, tmp_path, tmp_path, "synthetic", False
        )
        assert "LD_LIBRARY_PATH" not in server
        index = client.index("LD_LIBRARY_PATH")
        assert client[index - 1 : index + 2] == ["--setenv", "LD_LIBRARY_PATH", "/opt/python/lib"]
        for command in (server, client):
            assert "--clearenv" in command and "/untrusted-host-library" not in command
            assert command[command.index(str(tmp_path)) - 1] == "--ro-bind"
    finally:
        workspace.cleanup_if_settled()


@pytest.mark.parametrize("inherited", [None, "/untrusted-host-library"])
def test_python_helper_recovers_relocated_elf_without_exporting_library_path(tmp_path, inherited):
    """Use a tiny real ELF fixture; no image, database, model or namespace is started."""
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("C compiler unavailable for the offline ELF relocation regression")
    runtime = tmp_path / "original"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "lib").mkdir()
    library = tmp_path / "library.c"
    library.write_text("int kv_loader_fixture(void) { return 0; }\n")
    main = tmp_path / "main.c"
    main.write_text(
        "extern int kv_loader_fixture(void); int main(void) { return kv_loader_fixture(); }\n"
    )
    env = {"PATH": os.defpath, "LC_ALL": "C"}
    for command in (
        [
            compiler,
            "-shared",
            "-fPIC",
            str(library),
            "-o",
            str(runtime / "lib/libkv_loader_fixture.so"),
        ],
        [
            compiler,
            str(main),
            "-L" + str(runtime / "lib"),
            "-lkv_loader_fixture",
            "-Wl,-rpath," + str(runtime / "lib"),
            "-o",
            str(runtime / "bin/python3.12"),
        ],
    ):
        subprocess.run(  # noqa: S603  # Installed compiler; fixed fixture code and owned paths.
            command, env=env, capture_output=True, check=True, timeout=20
        )
    relocated = tmp_path / "relocated"
    runtime.rename(relocated)
    result = subprocess.run(  # noqa: S603  # Only the compiled fixture, not a server/Python.
        [str(relocated / "bin/python3.12")], env=env, capture_output=True, timeout=5
    )
    assert result.returncode == 127
    assert b"libkv_loader_fixture.so" in result.stderr

    script = (feasibility_spike.SUPPORT / "server.sh").read_text()
    start = script.index("python_runtime() {\n")
    helper = script[start : script.index("\n}", start) + 2]
    helper = helper.replace(
        "/opt/python/bin/python3.12", shlex.quote(str(relocated / "bin/python3.12"))
    ).replace("/opt/python/lib", shlex.quote(str(relocated / "lib")))
    caller_env = env if inherited is None else {**env, "LD_LIBRARY_PATH": inherited}
    result = subprocess.run(  # noqa: S603  # Actual fixed helper, fixture paths shell-quoted.
        ["/bin/sh", "-ec", helper + '\npython_runtime\nprintf "%s" "${LD_LIBRARY_PATH-unset}"'],
        env=caller_env,
        capture_output=True,
        check=True,
        timeout=5,
    )
    assert result.stdout == (inherited or "unset").encode() and result.stderr == b""


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
