import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WAIT_SCRIPT = ROOT / "scripts" / "ci" / "wait_for_postgres_database.sh"


def _fake_docker(tmp_path: Path) -> tuple[Path, Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    counter = tmp_path / "attempts"
    calls = tmp_path / "calls"
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/bin/sh
set -eu
counter=${FAKE_DOCKER_COUNTER:?}
printf '%s\n' "$*" >> "${FAKE_DOCKER_CALLS:?}"
case "$1" in
  exec)
    attempts=0
    if [ -f "$counter" ]; then attempts=$(cat "$counter"); fi
    attempts=$((attempts + 1))
    printf '%s\n' "$attempts" > "$counter"
    if [ "$FAKE_DOCKER_SUCCEEDS_ON" != never ] && \
      [ "$attempts" -ge "$FAKE_DOCKER_SUCCEEDS_ON" ]; then
      printf '1\n'
      exit 0
    fi
    printf 'database is still initializing\n' >&2
    exit 2
    ;;
  logs)
    printf 'synthetic PostgreSQL diagnostics\n'
    ;;
  *)
    exit 64
    ;;
esac
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return bin_dir, counter, calls


def _run_wait_script(
    tmp_path: Path, *, query_succeeds_on: int | None, attempts: int
) -> tuple[subprocess.CompletedProcess[str], str]:
    bin_dir, counter, calls = _fake_docker(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "FAKE_DOCKER_COUNTER": str(counter),
        "FAKE_DOCKER_CALLS": str(calls),
        "FAKE_DOCKER_SUCCEEDS_ON": (
            "never" if query_succeeds_on is None else str(query_succeeds_on)
        ),
        "POSTGRES_READY_ATTEMPTS": str(attempts),
        "POSTGRES_READY_SLEEP_SECONDS": "0",
    }
    result = subprocess.run(  # noqa: S603 - fixed test-only command and arguments
        ["/bin/sh", str(WAIT_SCRIPT), "postgres-test", "vault_user", "vault_db"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    call_log = calls.read_text(encoding="utf-8") if calls.exists() else ""
    return result, call_log


def test_postgres_wait_retries_until_the_named_database_is_queryable(tmp_path: Path) -> None:
    result, calls = _run_wait_script(tmp_path, query_succeeds_on=3, attempts=5)

    assert result.returncode == 0
    assert result.stderr.count("Waiting for PostgreSQL database vault_db") == 2
    assert calls.count("exec postgres-test psql") == 3
    assert "--username vault_user --dbname vault_db --command SELECT 1;" in calls
    assert "pg_isready" not in calls


def test_postgres_wait_times_out_with_container_diagnostics(tmp_path: Path) -> None:
    result, calls = _run_wait_script(tmp_path, query_succeeds_on=None, attempts=2)

    assert result.returncode == 1
    assert "PostgreSQL database vault_db did not become queryable" in result.stderr
    assert "synthetic PostgreSQL diagnostics" in result.stderr
    assert calls.endswith("logs --tail 100 postgres-test\n")


def test_postgres_wait_rejects_an_invalid_attempt_limit(tmp_path: Path) -> None:
    result, calls = _run_wait_script(tmp_path, query_succeeds_on=1, attempts=0)

    assert result.returncode == 2
    assert "POSTGRES_READY_ATTEMPTS must be a positive integer" in result.stderr
    assert calls == ""
