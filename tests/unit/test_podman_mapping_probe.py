"""The optional Mac probe may create only synthetic fixtures and must not pull or delete."""

import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/standalone/probe-podman-macos-mapping.sh"


def test_probe_creates_private_fixture_and_never_pulls_or_removes(tmp_path):
    tools = tmp_path / "bin"
    tools.mkdir()
    (tools / "uname").write_text("#!/bin/sh\necho Darwin\n")
    (tools / "podman").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$PROBE_LOG"\n'
        'case "$*" in *"--user 999:999"*) exit 1;; esac\nexit 0\n'
    )
    for command in tools.iterdir():
        command.chmod(0o755)
    log = tmp_path / "calls"
    env = os.environ | {
        "PATH": f"{tools}:/usr/bin:/bin",
        "PROBE_LOG": str(log),
        "KV_PROBE_PARENT": str(tmp_path),
    }
    result = subprocess.run(  # noqa: S603
        ["/bin/sh", str(SCRIPT)], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert "UID 999: not verified" in result.stdout
    calls = log.read_text().splitlines()
    runs = [line for line in calls if line.startswith("run ")]
    assert len(runs) == 3 and all(
        "--pull=never" in line and "--network=none" in line for line in runs
    )
    assert not any("--rm" in line or line.startswith(("rm ", "rmi ", "prune ")) for line in calls)
    fixture = next(tmp_path.glob("kv-mapping-probe.*"))
    assert stat.S_IMODE(fixture.stat().st_mode) == 0o700
    assert stat.S_IMODE((fixture / "marker").stat().st_mode) == 0o600
