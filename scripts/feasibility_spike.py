"""Run the synthetic local feasibility probe against a pre-staged image filesystem."""

import argparse
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.feasibility_diagnostics import StartupDiagnostics
from scripts.feasibility_image import verify_layers
from scripts.feasibility_lifecycle import RunError, run_pair
from scripts.feasibility_workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
SUPPORT = ROOT / "scripts" / "feasibility_support"
IMAGE_MOUNT_TARGETS = ("opt/python", "opt/site", "opt/repo")


def prepare_mount_directories(image_root: Path) -> None:
    """Create mount points without following any extracted image symlink."""
    root_descriptor = os.open(image_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for target in IMAGE_MOUNT_TARGETS:
            descriptor = os.dup(root_descriptor)
            try:
                for component in target.split("/"):
                    with contextlib.suppress(FileExistsError):
                        os.mkdir(component, 0o755, dir_fd=descriptor)
                    child = os.open(
                        component,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=descriptor,
                    )
                    os.close(descriptor)
                    descriptor = child
            finally:
                os.close(descriptor)
    finally:
        os.close(root_descriptor)


def staging_command(staged_layers: Path, image_root: Path) -> list[str]:
    """Build the extraction sandbox with one writable host bind, the image root."""
    return [
        "/usr/bin/bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--clearenv",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind",
        "/lib64",
        "/lib64",
        "--ro-bind",
        str(staged_layers),
        "/layers",
        "--ro-bind",
        str(SUPPORT / "unpack.py"),
        "/unpack.py",
        "--bind",
        str(image_root),
        "/rootfs",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",  # noqa: S108  # Namespace-private tmpfs.
        "/usr/bin/python3",
        "/unpack.py",
    ]


def prepare_run_commands(
    workspace: Workspace,
    image_root: Path,
    python_root: Path,
    site: Path,
    revision: str,
    dirty: bool,
) -> tuple[list[str], list[str], Path]:
    """Prepare host paths and sandbox commands, cleaning only a settled workspace on failure."""
    try:
        prepare_mount_directories(image_root)
        shared = workspace.root / "client"
        shared.mkdir(mode=0o700)
        telemetry = workspace.root / "telemetry"
        telemetry.mkdir(mode=0o700)
        common = [
            "/usr/bin/bwrap",
            "--unshare-all",
            "--die-with-parent",
            "--clearenv",
            "--ro-bind",
            str(image_root),
            "/",
            "--ro-bind",
            str(python_root),
            f"/{IMAGE_MOUNT_TARGETS[0]}",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",  # noqa: S108  # Namespace-private tmpfs.
            "--bind",
            str(shared),
            "/tmp/client",  # noqa: S108  # Owner-only client socket mount.
        ]
        server = [
            *common,
            "--ro-bind",
            str(SUPPORT / "passwd"),
            "/etc/passwd",
            "--ro-bind",
            str(SUPPORT / "group"),
            "/etc/group",
            "--ro-bind",
            str(site),
            f"/{IMAGE_MOUNT_TARGETS[1]}",
            "--ro-bind",
            str(ROOT),
            f"/{IMAGE_MOUNT_TARGETS[2]}",
            "--bind",
            str(telemetry),
            "/tmp/telemetry",  # noqa: S108  # Server-only telemetry mount.
            "--bind",
            str(workspace.root / "diagnostics/startup.status"),
            "/tmp/startup.status",  # noqa: S108  # Single server-only diagnostic file.
            "--setenv",
            "KV_SOURCE_REVISION",
            revision,
            "--setenv",
            "KV_SOURCE_DIRTY",
            "1" if dirty else "0",
            "--ro-bind",
            str(SUPPORT / "server.py"),
            "/tmp/probe.py",  # noqa: S108  # Private namespace mount.
            "--ro-bind",
            str(SUPPORT / "server.sh"),
            "/tmp/run.sh",  # noqa: S108  # Private namespace mount.
            "/bin/sh",
            "/tmp/run.sh",  # noqa: S108  # Private namespace mount.
        ]
        client = [
            *common,
            "--ro-bind",
            str(SUPPORT / "client.py"),
            "/tmp/client-probe.py",  # noqa: S108  # Private namespace mount.
            "--setenv",
            "LC_ALL",
            "C",
            "--setenv",
            "LD_LIBRARY_PATH",
            f"/{IMAGE_MOUNT_TARGETS[0]}/lib",
            f"/{IMAGE_MOUNT_TARGETS[0]}/bin/python3.12",
            "/tmp/client-probe.py",  # noqa: S108  # Private namespace mount.
        ]
        return server, client, telemetry
    except BaseException:
        workspace.cleanup_if_settled()
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-layers", type=Path, required=True)
    args = parser.parse_args()
    layers = args.image_layers.resolve(strict=True)
    if os.getuid() != 1000 or os.getgid() != 1000:
        raise RuntimeError("the disposable image passwd mapping requires uid and gid 1000")
    layer_names = verify_layers(layers)
    python_root = Path(sys._base_executable).resolve().parent.parent
    site = ROOT / ".venv/lib/python3.12/site-packages"
    if not site.is_dir() or sys.version_info[:2] != (3, 12):
        raise RuntimeError("run with this checkout's locked Python 3.12 environment")
    revision = subprocess.check_output(  # noqa: S603  # Fixed executable and arguments.
        ["/usr/bin/git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(  # noqa: S603  # Fixed executable and arguments.
            ["/usr/bin/git", "-C", str(ROOT), "status", "--porcelain"], text=True
        ).strip()
    )

    workspace = Workspace.create(Path("/tmp"))  # noqa: S108  # mkdtemp creates an owned directory.
    image_root = workspace.root / "rootfs"
    process = None
    launch_attempted = False
    diagnostics = None
    try:
        diagnostic_directory = workspace.root / "diagnostics"
        diagnostic_directory.mkdir(mode=0o700)
        diagnostics = StartupDiagnostics(diagnostic_directory)
        image_root.mkdir(mode=0o700)
        staged_layers = workspace.root / "layers"
        staged_layers.mkdir(mode=0o700)
        descriptor = os.open(layers / "manifest.json", os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as source:
            manifest = source.read(1_000_001)
        if len(manifest) > 1_000_000:
            raise RuntimeError("image manifest grew while copying")
        (staged_layers / "manifest.json").write_bytes(manifest)
        for name, size in layer_names:
            descriptor = os.open(layers / name, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as source, (staged_layers / name).open("xb") as out:
                remaining = size
                while remaining:
                    chunk = source.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise RuntimeError("image layer truncated while copying")
                    out.write(chunk)
                    remaining -= len(chunk)
                if source.read(1):
                    raise RuntimeError("image layer grew while copying")
        verify_layers(staged_layers)
        launch_attempted = True
        workspace.mark_launched()
        diagnostics.mark("image_staging")
        process = subprocess.Popen(  # noqa: S603  # Fixed command and verified layers.
            staging_command(staged_layers, image_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if process.wait(timeout=120) != 0:
            raise RuntimeError("isolated image staging failed")
        diagnostics.mark("image_staging", "completed")
        workspace.mark_settled()
    except BaseException as exc:
        observed_returncode = process.poll() if process is not None else None
        settled = process is None or process.poll() is not None
        if process is not None and not settled:
            try:
                process.kill()
                process.wait(timeout=10)
                settled = process.poll() is not None
            except BaseException:
                settled = False
        if diagnostics is not None:
            diagnostics.capture(
                observed_returncode,
                (
                    "staging_timeout"
                    if isinstance(exc, subprocess.TimeoutExpired)
                    else "staging_error"
                ),
                settled,
            )
            print(json.dumps({"startup": diagnostics.report}))
            diagnostics.close()
        if settled:
            if launch_attempted:
                marker = workspace.root / "child-unsettled"
                if marker.exists() or marker.is_symlink():
                    workspace.mark_settled()
            workspace.cleanup()
        raise SystemExit(1) from None
    try:
        server, client, telemetry = prepare_run_commands(
            workspace, image_root, python_root, site, revision, dirty
        )
    except BaseException:
        diagnostics.close()
        raise

    def emit_trace() -> None:
        trace = telemetry / "trace.json"
        if trace.is_symlink() or not trace.is_file() or trace.stat().st_size > 100_000:
            raise RuntimeError("synthetic telemetry unavailable")
        print(json.dumps(json.loads(trace.read_text(encoding="utf-8"))))

    try:
        run_pair(
            workspace, server, client, timeout=60, on_success=emit_trace, diagnostics=diagnostics
        )
    except Exception as exc:
        is_timeout = isinstance(exc, RunError) and (
            "timeout" in str(exc) or "timed out" in str(exc)
        )
        state = "timeout" if is_timeout else "failure"
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "source_revision": revision,
                    "source_dirty": dirty,
                    "state": state,
                    "startup": diagnostics.report,
                    "gateway": None,
                    "agent_selected_ids": None,
                    "model_visible_context": None,
                    "provider_usage": None,
                }
            )
        )
        raise SystemExit(1) from None
    finally:
        if diagnostics.report:
            print(json.dumps({"startup": diagnostics.report}))
        diagnostics.close()
    print("synthetic isolated feasibility smoke passed")


if __name__ == "__main__":
    main()
