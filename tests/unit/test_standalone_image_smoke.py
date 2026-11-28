import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/ci/standalone_image_smoke.sh"


def fake_docker(
    tmp_path: Path, fail: str = "", engine: str = "docker"
) -> tuple[Path, dict[str, str]]:
    docker = tmp_path / engine
    log = tmp_path / "docker.log"
    docker.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$DOCKER_LOG"\n'
        'if [ "$FAIL_MODE" = inspect ] && [ "$1" = image ]; then exit 19; fi\n'
        'if [ "$FAIL_MODE" = run ] && [ "$1" = run ]; then exit 20; fi\n'
        'if [ "$FAIL_MODE" = backup_command ] && [ "$1" = run ]; then\n'
        '  case "$*" in *sha256:backup*) for last do :; done; /bin/sh -c "$last"; exit $?;; esac\n'
        "fi\n"
        'case "$*" in\n'
        "  *'{{.Config.User}}'*sha256:app*) echo 10001:10001;;\n"
        "  *'{{.Config.User}}'*sha256:backup*) echo 999:999;;\n"
        "  *'{{.User}}'*sha256:app*) echo 10001:10001;;\n"
        "  *'{{.User}}'*sha256:backup*) echo 999:999;;\n"
        "  *'{{.Id}} {{.Os}}/{{.Architecture}}'*) echo 'metadata linux/amd64';;\n"
        "  *'{{.Id}}'*app:local*) echo sha256:app;;\n"
        "  *'{{.Id}}'*backup:local*) echo sha256:backup;;\n"
        "  *'{{.Architecture}}'*) echo x86_64;;\n"
        "  *'{{.Server.Version}}'*) echo test;;\n"
        "esac\n"
    )
    docker.chmod(0o755)
    return log, os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
        "FAIL_MODE": fail,
        "KV_SMOKE_ENGINE": engine,
    }


def test_smoke_uses_resolved_images_and_isolates_containers(tmp_path: Path) -> None:
    log, env = fake_docker(tmp_path)
    model = tmp_path / "local model"
    model.mkdir()
    result = subprocess.run(  # noqa: S603 - fixed script and isolated test paths
        ["/bin/sh", str(SCRIPT), "app:local", "backup:local", str(model)],
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    runs = [line for line in log.read_text().splitlines() if line.startswith("run ")]
    assert len(runs) == 2
    assert all("--network=none" in line and "--read-only" in line for line in runs)
    assert all("--pull=never" in line and "--cap-drop=ALL" in line for line in runs)
    assert "--tmpfs /tmp:rw,nosuid,nodev,size=128m,mode=1777" in runs[0]
    assert "--tmpfs" not in runs[1]
    assert "sha256:app" in runs[0]
    assert "sha256:backup" in runs[1]
    assert "set -e; test" in runs[1]
    assert "pg_dump --version; restic version" in runs[1]
    assert "dst=/model,readonly" in runs[0]
    assert "matching reported architectures alone does not rule out emulation" in result.stdout


def test_podman_uses_podman_metadata_and_same_isolation(tmp_path: Path) -> None:
    log, env = fake_docker(tmp_path, engine="podman")
    model = tmp_path / "model"
    model.mkdir()
    subprocess.run(  # noqa: S603 - fixed script and isolated test paths
        ["/bin/sh", str(SCRIPT), "app:local", "backup:local", str(model)],
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    calls = log.read_text().splitlines()
    assert any("{{.Host.Arch}}" in call for call in calls)
    assert any("{{.User}}" in call for call in calls)
    assert not any("{{.Config.User}}" in call for call in calls)
    runs = [line for line in calls if line.startswith("run ")]
    assert len(runs) == 2
    assert all("--read-only-tmpfs=false" in line for line in runs)
    assert all("--network=none" in line and "--read-only" in line for line in runs)
    assert all("--pull=never" in line and "--cap-drop=ALL" in line for line in runs)
    assert "sha256:app" in runs[0] and "sha256:backup" in runs[1]


def test_smoke_rejects_unrecognized_engine(tmp_path: Path) -> None:
    model = tmp_path / "model"
    model.mkdir()
    result = subprocess.run(  # noqa: S603 - fixed script and isolated test paths
        ["/bin/sh", str(SCRIPT), "app:local", "backup:local", str(model)],
        env=os.environ | {"KV_SMOKE_ENGINE": "unexpected"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "KV_SMOKE_ENGINE must be docker or podman" in result.stderr


def test_smoke_rejects_missing_model_before_running_docker(tmp_path: Path) -> None:
    result = subprocess.run(  # noqa: S603 - fixed script and isolated test paths
        ["/bin/sh", str(SCRIPT), "app", "backup", str(tmp_path / "missing")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "local model directory is required" in result.stderr


def test_smoke_stops_after_inspect_or_application_failure(tmp_path: Path) -> None:
    model = tmp_path / "model"
    model.mkdir()
    for mode, expected_runs, code in (("inspect", 0, 19), ("run", 1, 20)):
        log, env = fake_docker(tmp_path, mode)
        log.unlink(missing_ok=True)
        result = subprocess.run(  # noqa: S603 - fixed script and isolated test paths
            ["/bin/sh", str(SCRIPT), "app:local", "backup:local", str(model)],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == code
        assert len([line for line in log.read_text().splitlines() if line.startswith("run ")]) == (
            expected_runs
        )


def test_smoke_rejects_bad_arguments_and_ambiguous_mount(tmp_path: Path) -> None:
    model = tmp_path / "model,unsafe"
    model.mkdir()
    for args in ([], ["app", "backup", str(model)]):
        result = subprocess.run(  # noqa: S603 - fixed script and isolated test paths
            ["/bin/sh", str(SCRIPT), *args], text=True, capture_output=True, check=False
        )
        assert result.returncode == 2


def test_backup_smoke_stops_when_pg_dump_fails(tmp_path: Path) -> None:
    log, env = fake_docker(tmp_path, "backup_command")
    model = tmp_path / "model"
    model.mkdir()
    for name, content in {
        "id": "echo 999",
        "uname": "echo test",
        "pg_dump": "exit 17",
        "restic": 'touch "$DOCKER_LOG.restic"',
    }.items():
        executable = tmp_path / name
        executable.write_text(f"#!/bin/sh\n{content}\n")
        executable.chmod(0o755)
    result = subprocess.run(  # noqa: S603 - fixed script and isolated test paths
        ["/bin/sh", str(SCRIPT), "app:local", "backup:local", str(model)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 17
    assert not Path(f"{log}.restic").exists()
