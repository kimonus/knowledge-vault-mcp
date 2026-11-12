"""Command-contract tests with fake tools; these are not disaster-recovery tests."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "deploy/standalone"


def _fake_tools(tmp_path):
    tools = tmp_path / "bin"
    tools.mkdir()
    commands = {
        "pg_dump": 'printf "dump" > "${5#--file=}"',
        "restic": ":",
        "psql": ":",
        "pg_restore": ":",
    }
    for name, body in commands.items():
        path = tools / name
        path.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$(basename "$0") $*" >> "$TEST_LOG"\n' + body + "\n"
        )
        path.chmod(0o755)
    return tools


def _env(tmp_path, tools):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return os.environ | {
        "PATH": f"{tools}:/usr/bin:/bin",
        "PGSERVICEFILE": "/test/service",
        "PGPASSFILE": "/test/password",
        "RESTIC_PASSWORD_FILE": "/test/restic-password",
        "RESTIC_SNAPSHOT_ID": "a" * 64,
        "SCRATCH_DIR": str(scratch),
        "TEST_LOG": str(tmp_path / "log"),
    }


def _run(name, env):
    return subprocess.run(  # noqa: S603
        ["/bin/sh", str(SCRIPTS / name)], env=env, capture_output=True, text=True, check=False
    )


def test_backup_never_prunes_or_overwrites(tmp_path):
    env = _env(tmp_path, _fake_tools(tmp_path))
    assert _run("backup-safe.sh", env).returncode == 0
    log = (tmp_path / "log").read_text()
    assert "pg_dump --dbname=service=backup" in log
    assert "restic backup " in log
    assert "restic forget " not in log and "restic prune " not in log
    assert _run("backup-safe.sh", env).returncode != 0
    assert (tmp_path / "log").read_text() == log


def test_restore_always_refuses_without_invoking_any_tool(tmp_path):
    env = _env(tmp_path, _fake_tools(tmp_path))
    result = _run("restore-safe.sh", env)
    assert result.returncode != 0
    assert "disabled" in result.stderr
    assert not (tmp_path / "log").exists()
    assert list((tmp_path / "scratch").iterdir()) == []
