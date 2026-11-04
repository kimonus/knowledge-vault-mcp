"""Run the synthetic local feasibility probe against a pre-staged image filesystem."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.feasibility_image import verify_layers
from scripts.feasibility_lifecycle import run_pair
from scripts.feasibility_workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
SUPPORT = ROOT / "scripts" / "feasibility_support"


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
    image_root.mkdir(mode=0o700)
    workspace.mark_launched()
    process = None
    try:
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
        process = subprocess.Popen(  # noqa: S603  # Fixed command and verified layers.
            [
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
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if process.wait(timeout=120) != 0:
            raise RuntimeError("isolated image staging failed")
    except BaseException:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        workspace.mark_settled()
        workspace.cleanup()
        raise
    workspace.mark_settled()
    for destination in ("python", "site", "repo"):
        (image_root / "opt" / destination).mkdir(parents=True, exist_ok=True)
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
        "/opt/python",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",  # noqa: S108  # Private namespace tmpfs.
        "--bind",
        str(shared),
        "/tmp/client",  # noqa: S108  # Private namespace mount.
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
        "/opt/site",
        "--ro-bind",
        str(ROOT),
        "/opt/repo",
        "--bind",
        str(telemetry),
        "/tmp/telemetry",  # noqa: S108  # Server-only private mount.
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
        "/opt/python/bin/python3.12",
        "/tmp/client-probe.py",  # noqa: S108  # Private namespace mount.
    ]

    def emit_trace() -> None:
        trace = telemetry / "trace.json"
        if trace.is_symlink() or not trace.is_file() or trace.stat().st_size > 100_000:
            raise RuntimeError("synthetic telemetry unavailable")
        print(json.dumps(json.loads(trace.read_text(encoding="utf-8"))))

    run_pair(workspace, server, client, timeout=60, on_success=emit_trace)
    print("synthetic isolated feasibility smoke passed")


if __name__ == "__main__":
    main()
