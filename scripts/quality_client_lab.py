#!/usr/bin/env python3
"""Disposable, loopback-only infrastructure for operator-run MCP client evaluation."""

import argparse
import asyncio
import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx2
import uvicorn
from alembic import command
from alembic.config import Config
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import create_engine, text

from knowledge_vault.api.app import create_app
from knowledge_vault.config import Settings
from knowledge_vault.container import build_container

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_URLS: set[str] = set()
IMAGE = (
    "pgvector/pgvector:0.8.1-pg17-trixie@"
    "sha256:137f044b0efe3d57f39b972b9b53641b1f2045b99d879e298bbf514a25787dcf"
)
TABLES = (
    "confirmation_tokens, deletion_audit, knowledge_conflicts, embedding_jobs, "
    "flush_parts, flush_batches, assertion_sources, assertions, artifacts, "
    "knowledge_contexts, topic_aliases, temporal_retirements, temporal_evidence"
)


class LabInterrupted(Exception):
    """An operator signal requested a clean end to this disposable run."""


def local_mcp_url(base: str) -> str:
    parsed = urlsplit(base)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port is None
        or parsed.path not in {"", "/"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("MCP client URL must be plain HTTP on 127.0.0.1")
    return f"http://127.0.0.1:{parsed.port}/mcp"


def local_http_client(*, token: str | None = None, timeout: float = 30) -> httpx2.AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else None
    # httpx2 defaults trust_env=True, which can route even loopback through an inherited proxy
    # when NO_PROXY is changed. The SDK uses this supplied client for every MCP request.
    return httpx2.AsyncClient(
        headers=headers,
        timeout=timeout,
        trust_env=False,
        proxy=None,
        follow_redirects=False,
    )


def refuse_inherited_configuration(environ: dict[str, str]) -> None:
    forbidden = sorted(
        key
        for key in environ
        if key.startswith(("KNOWLEDGE_VAULT_", "PG", "HF_", "HUGGINGFACE_"))
        or key in {"DATABASE_URL", "DOCKER_HOST", "DOCKER_CONTEXT", "CODEX_HOME", "MCP_CONFIG"}
    )
    if forbidden:
        raise RuntimeError("unsafe inherited configuration: " + ", ".join(forbidden))


def docker(*args: str, timeout: float = 30) -> str:
    # Pin the local socket; never follow a caller's Docker context or remote daemon.
    result = subprocess.run(  # noqa: S603  # Fixed executable, harness-generated arguments.
        ["/usr/bin/docker", "-H", "unix:///var/run/docker.sock", *args],
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        timeout=timeout,
    )
    return (result.stdout + (result.stderr if args[0] == "logs" else "")).strip()


def check_host() -> dict[str, object]:
    refuse_inherited_configuration(dict(os.environ))
    info = json.loads(docker("info", "--format", "{{json .}}"))
    if info["DockerRootDir"] != "/opt/docker" or info["Name"] != "homelab":
        raise RuntimeError("unexpected Docker daemon; inspect before proceeding")
    stat = os.statvfs(info["DockerRootDir"])
    free = stat.f_bavail * stat.f_frsize
    if free < 10 * 1024**3:
        raise RuntimeError("Docker data root has less than 10 GiB free")
    with open("/proc/meminfo", encoding="ascii") as handle:
        memory = {
            key: int(value.split()[0]) * 1024
            for line in handle
            if (parts := line.split(":", 1))
            for key, value in [parts]
        }
    if memory["MemAvailable"] < 4 * 1024**3:
        raise RuntimeError("less than 4 GiB memory available")
    docker("image", "inspect", IMAGE, "--format", "{{.Id}}")
    return {"docker_root_free_bytes": free, "memory_available_bytes": memory["MemAvailable"]}


def owned_resource(kind: str, name: str, owner: str) -> bool:
    try:
        label = docker(
            *(("inspect", name) if kind == "container" else ("network", "inspect", name)),
            "--format",
            '{{index .Config.Labels "kv.client-lab.owner"}}'
            if kind == "container"
            else '{{index .Labels "kv.client-lab.owner"}}',
        )
    except subprocess.CalledProcessError:
        return False
    return label == owner


def remove_owned_container(container: str, owner: str) -> None:
    if owned_resource("container", container, owner):
        docker("rm", "-f", "-v", container)
    elif docker("ps", "-a", "--filter", f"name=^{container}$", "--format", "{{.Names}}"):
        raise RuntimeError("container name exists without expected owner label")


def remove_owned_socket(socket_dir: Path, owner: str) -> None:
    if not socket_dir.exists() and not socket_dir.is_symlink():
        return
    marker = socket_dir / ".kv-client-lab-owner"
    if (
        socket_dir.parent != Path("/tmp")  # noqa: S108  # Fixed root for private mkdtemp.
        or not socket_dir.name.startswith(f"kv-client-lab-{owner}-")
        or socket_dir.is_symlink()
        or socket_dir.stat().st_uid != os.getuid()
        or not marker.is_file()
        or marker.is_symlink()
        or marker.read_text(encoding="ascii") != owner
    ):
        raise RuntimeError("socket directory is not positively owned by this lab run")
    shutil.rmtree(socket_dir)


def check_interruption(stop_requested: Callable[[], bool] | None) -> None:
    if stop_requested is not None and stop_requested():
        raise LabInterrupted


def prepare_output(output: Path, owner: str, socket_dir: Path) -> None:
    if output.resolve().is_relative_to(ROOT):
        raise RuntimeError("output directory must be outside the repository")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    marker = output / ".kv-client-lab-owner"
    manifest = output / "environment.json"
    marker.write_text(owner, encoding="ascii")
    manifest.write_text(json.dumps({"lab_owner": owner, "db_socket_dir": str(socket_dir)}) + "\n")
    for path in (marker, manifest):
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    write_lifecycle(
        output,
        owner,
        {
            "launch": "pending",
            "cleanup": "pending",
            "settled_confirmed": False,
            "parent": process_identity(os.getpid()),
            "watcher": None,
            "socket_dir": str(socket_dir),
        },
    )
    parent_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def default_smoke_output(owner: str) -> Path:
    root = Path.home() / ".local" / "state" / "knowledge-vault" / "client-lab"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise RuntimeError("smoke recovery root is not private and owned by this user")
    return root / f"smoke-{owner}"


def owned_output(output: Path, owner: str) -> bool:
    marker = output / ".kv-client-lab-owner"
    return (
        not output.is_symlink()
        and output.stat().st_uid == os.getuid()
        and marker.is_file()
        and not marker.is_symlink()
        and marker.read_text(encoding="ascii") == owner
    )


def process_identity(pid: int) -> list[int]:
    # The start tick distinguishes a live process from a later reuse of its PID.
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    return [pid, int(stat.rsplit(") ", 1)[1].split()[19])]


def process_active(identity: list[int]) -> bool:
    try:
        return process_identity(identity[0]) == identity
    except FileNotFoundError:
        return False


def write_lifecycle(output: Path, owner: str, state: dict[str, object]) -> None:
    if not owned_output(output, owner):
        raise RuntimeError("output directory is not positively owned by this lab run")
    path = output / ".lifecycle-status"
    temporary = output / f".lifecycle-status-{uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            json.dump(state, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        directory_fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def read_lifecycle(output: Path, owner: str) -> dict[str, object]:
    if not owned_output(output, owner):
        raise RuntimeError("output directory is not positively owned by this lab run")
    try:
        path = output / ".lifecycle-status"
        if path.is_symlink():
            raise RuntimeError("invalid lab lifecycle status")
        state = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError("missing or invalid lab lifecycle status") from exc
    if not isinstance(state, dict) or set(state) != {
        "launch",
        "cleanup",
        "settled_confirmed",
        "parent",
        "watcher",
        "socket_dir",
    }:
        raise RuntimeError("invalid lab lifecycle status")

    def valid_identity(value: object) -> bool:
        return (
            isinstance(value, list)
            and len(value) == 2
            and all(type(item) is int and item > 0 for item in value)
        )

    if (
        not isinstance(state["launch"], str)
        or state["launch"] not in {"pending", "started", "failed_safe", "uncertain"}
        or not isinstance(state["cleanup"], str)
        or state["cleanup"] not in {"pending", "incomplete", "complete"}
        or type(state["settled_confirmed"]) is not bool
        or not isinstance(state["socket_dir"], str)
        or not valid_identity(state["parent"])
        or (state["watcher"] is not None and not valid_identity(state["watcher"]))
        or (state["cleanup"] == "complete" and state["launch"] == "pending")
        or (
            state["cleanup"] == "complete"
            and state["launch"] == "uncertain"
            and not state["settled_confirmed"]
        )
        or (state["settled_confirmed"] and state["cleanup"] != "complete")
        or (state["settled_confirmed"] and state["launch"] != "uncertain")
    ):
        raise RuntimeError("contradictory lab lifecycle status")
    return state


def cleanup_owned(owner: str, socket_dir: Path, output: Path | None) -> None:
    remove_owned_container(f"kv-client-lab-db-{owner}", owner)
    output_error = None
    if output is not None:
        if output.exists() and owned_output(output, owner):
            (output / "lab-token").unlink(missing_ok=True)
        elif output.exists():
            output_error = RuntimeError("output directory is not positively owned by this lab run")
    remove_owned_socket(socket_dir, owner)
    if output_error is not None:
        raise output_error


def postgres_run_args(owner: str, socket_dir: Path, password: str) -> tuple[str, ...]:
    return (
        "run",
        "-d",
        "--name",
        f"kv-client-lab-db-{owner}",
        "--label",
        f"kv.client-lab.owner={owner}",
        "--network",
        "none",
        "--mount",
        f"type=bind,source={socket_dir},target=/var/run/postgresql",
        "--memory",
        "768m",
        "--cpus",
        "1",
        "--pids-limit",
        "128",
        "--cap-drop",
        "ALL",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        f"/var/lib/postgresql/data:rw,nosuid,noexec,size=512m,uid={os.getuid()},gid={os.getgid()}",
        "--env",
        "POSTGRES_USER=lab",
        "--env",
        f"POSTGRES_PASSWORD={password}",
        "--env",
        "POSTGRES_DB=lab",
        IMAGE,
        "postgres",
        "-c",
        "listen_addresses=",
    )


def start_watcher(
    owner: str, socket_dir: Path, output: Path | None, password: str
) -> tuple[int, subprocess.Popen[bytes]]:
    lock_path = socket_dir / ".parent-lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    password_path = socket_dir / ".db-password"
    args = [
        sys.executable,
        str(Path(__file__).resolve()),
        "watch",
        "--owner",
        owner,
        "--socket-dir",
        str(socket_dir),
    ]
    if output is not None:
        args.extend(("--output", str(output)))
    watcher: subprocess.Popen[bytes] | None = None
    try:
        password_fd = os.open(password_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(password_fd, "w", encoding="ascii") as handle:
            handle.write(password)
        with (socket_dir / ".watcher-error").open("w", encoding="utf-8") as log:
            watcher = subprocess.Popen(  # noqa: S603  # Fixed Python executable and generated paths.
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log,
                start_new_session=True,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            )
    except BaseException:
        if watcher is not None:
            # A logging-file close failure after Popen must not orphan a live launcher.
            return descriptor, watcher
        os.close(descriptor)
        raise
    assert watcher is not None
    return descriptor, watcher


def wait_for_launch(
    watcher: subprocess.Popen[bytes],
    socket_dir: Path,
    stop_requested: Callable[[], bool] | None,
) -> None:
    status_path = socket_dir / ".launch-status"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        check_interruption(stop_requested)
        if status_path.exists():
            if status_path.read_text(encoding="ascii") == "started":
                return
            raise RuntimeError("disposable Docker startup failed; cleanup outcome is being checked")
        if watcher.poll() is not None:
            raise RuntimeError(
                "Docker launcher exited before reporting startup; cleanup is incomplete"
            )
        time.sleep(0.05)
    raise RuntimeError("Docker launch outcome is unknown after 90 seconds; cleanup is incomplete")


def validate_watcher_start(owner: str, socket_dir: Path, output: Path | None) -> dict[str, object]:
    if (
        not re.fullmatch(r"[0-9a-f]{32}", owner)
        or socket_dir.parent != Path("/tmp")  # noqa: S108
        or not re.fullmatch(rf"kv-client-lab-{owner}-[a-z0-9_]{{8}}", socket_dir.name)
        or socket_dir.is_symlink()
        or not socket_dir.is_dir()
        or socket_dir.stat().st_uid != os.getuid()
        or socket_dir.stat().st_mode & 0o077
    ):
        raise RuntimeError("watcher refused an invalid lab socket directory")
    marker = socket_dir / ".kv-client-lab-owner"
    lock = socket_dir / ".parent-lock"
    auth_file = socket_dir / ".db-password"
    if (
        not marker.is_file()
        or marker.is_symlink()
        or marker.read_text(encoding="ascii") != owner
        or not lock.is_file()
        or lock.is_symlink()
        or lock.stat().st_uid != os.getuid()
        or lock.stat().st_mode & 0o077
        or not auth_file.is_file()
        or auth_file.is_symlink()
        or auth_file.stat().st_uid != os.getuid()
        or auth_file.stat().st_mode & 0o077
    ):
        raise RuntimeError("watcher refused an unowned socket directory")
    if output is None or not owned_output(output, owner):
        raise RuntimeError("watcher requires an owned recovery directory")
    manifest = json.loads((output / "environment.json").read_text(encoding="ascii"))
    lifecycle = read_lifecycle(output, owner)
    if (
        not isinstance(manifest, dict)
        or manifest.get("lab_owner") != owner
        or manifest.get("db_socket_dir") != str(socket_dir)
        or lifecycle["socket_dir"] != str(socket_dir)
        or lifecycle["launch"] != "pending"
        or lifecycle["cleanup"] != "pending"
        or lifecycle["watcher"] is not None
    ):
        raise RuntimeError("watcher refused inconsistent recovery state")
    return lifecycle


def watch_parent(owner: str, socket_dir: Path, output: Path | None) -> None:
    lifecycle = validate_watcher_start(owner, socket_dir, output)
    assert output is not None
    # One detached watcher may claim this run. A second direct invocation cannot launch Docker.
    try:
        claim = os.open(
            socket_dir / ".watcher-claim",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
    except FileExistsError as exc:
        raise RuntimeError("watcher already claimed this lab run") from exc
    os.close(claim)
    descriptor = os.open(socket_dir / ".parent-lock", os.O_RDONLY | os.O_NOFOLLOW)
    password_path = socket_dir / ".db-password"
    launch_complete = False
    attempted_launch = False
    lifecycle["watcher"] = process_identity(os.getpid())
    write_lifecycle(output, owner, lifecycle)
    try:
        try:
            password = password_path.read_text(encoding="ascii")
            password_path.unlink()
            # The detached watcher owns this CLI. Parent death cannot end it or race cleanup
            # against a container that the daemon is still creating.
            attempted_launch = True
            lifecycle["launch"] = "uncertain"
            write_lifecycle(output, owner, lifecycle)
            docker(*postgres_run_args(owner, socket_dir, password), timeout=75)
            launch_complete = True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, UnicodeError):
            pass
        lifecycle["launch"] = (
            "started" if launch_complete else "uncertain" if attempted_launch else "failed_safe"
        )
        write_lifecycle(output, owner, lifecycle)
        status_error: OSError | None = None
        try:
            status_path = socket_dir / ".launch-status"
            temporary_status = socket_dir / ".launch-status.tmp"
            temporary_status.write_text(
                "started" if launch_complete else "failed", encoding="ascii"
            )
            temporary_status.replace(status_path)
        except OSError as exc:
            status_error = exc
        # Kernel release also covers SIGKILL and a wrapper that terminates Python directly.
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            cleanup_owned(owner, socket_dir, output)
        except (
            RuntimeError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            lifecycle["cleanup"] = "incomplete"
            write_lifecycle(output, owner, lifecycle)
            if output.exists() and owned_output(output, owner):
                (output / ".cleanup-status").write_text(f"incomplete: {type(exc).__name__}\n")
            raise RuntimeError("owner-checked Docker cleanup did not complete") from exc
        lifecycle["cleanup"] = (
            "complete"
            if (launch_complete or not attempted_launch) and status_error is None
            else "incomplete"
        )
        write_lifecycle(output, owner, lifecycle)
        if (attempted_launch and not launch_complete) or status_error is not None:
            if output.exists() and owned_output(output, owner):
                (output / ".cleanup-status").write_text(
                    "incomplete: Docker launch or status outcome uncertain\n"
                )
            raise RuntimeError(
                "Docker launch did not complete successfully; recheck owned resources"
            )
        if output.exists() and owned_output(output, owner):
            (output / ".cleanup-status").write_text("complete\n")
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def disposable_postgres(
    stop_requested: Callable[[], bool] | None = None, output: Path | None = None
):
    owner = uuid4().hex
    container = f"kv-client-lab-db-{owner}"
    password = secrets.token_urlsafe(32)
    socket_dir = Path(tempfile.mkdtemp(prefix=f"kv-client-lab-{owner}-", dir="/tmp"))
    socket_dir.chmod(0o700)
    url = f"postgresql+psycopg://lab:{quote(password)}@/lab?host={quote(str(socket_dir), safe='/')}"
    lock_descriptor: int | None = None
    watcher: subprocess.Popen[bytes] | None = None
    try:
        (socket_dir / ".kv-client-lab-owner").write_text(owner, encoding="ascii")
        if output is None:
            output = default_smoke_output(owner)
        prepare_output(output, owner, socket_dir)
        print(f"Lab recovery directory: {output}", flush=True)
        lock_descriptor, watcher = start_watcher(owner, socket_dir, output, password)
        wait_for_launch(watcher, socket_dir, stop_requested)
        check_interruption(stop_requested)
        for _ in range(60):
            check_interruption(stop_requested)
            try:
                probe = subprocess.run(  # noqa: S603  # Fixed executable, owned container.
                    [
                        "/usr/bin/docker",
                        "-H",
                        "unix:///var/run/docker.sock",
                        "exec",
                        container,
                        "pg_isready",
                        "-h",
                        "/var/run/postgresql",
                        "-U",
                        "lab",
                        "-d",
                        "lab",
                    ],
                    capture_output=True,
                    env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                    timeout=5,
                )
            except subprocess.TimeoutExpired:
                check_interruption(stop_requested)
            else:
                if probe.returncode == 0:
                    break
            check_interruption(stop_requested)
            time.sleep(0.5)
        else:
            state = docker(
                "inspect", container, "--format", "{{.State.Status}}/{{.State.ExitCode}}"
            )
            startup = docker("logs", "--tail", "8", container)
            raise RuntimeError(f"disposable PostgreSQL did not become ready: {state}; {startup}")
        if docker("inspect", container, "--format", "{{.HostConfig.NetworkMode}}") != "none":
            raise RuntimeError("database container has networking enabled")
        mounts = json.loads(docker("inspect", container, "--format", "{{json .Mounts}}"))
        if (
            len(mounts) != 1
            or mounts[0]["Source"] != str(socket_dir)
            or mounts[0]["Destination"] != "/var/run/postgresql"
        ):
            raise RuntimeError("database container has unexpected mounts")
        ACTIVE_URLS.add(url)
        yield url, owner, container, socket_dir
    finally:
        ACTIVE_URLS.discard(url)
        if lock_descriptor is None or watcher is None:
            cleanup_owned(owner, socket_dir, output)
            if output is not None:
                state = read_lifecycle(output, owner)
                state["launch"] = "failed_safe"
                state["cleanup"] = "complete"
                write_lifecycle(output, owner, state)
        else:
            os.close(lock_descriptor)
            try:
                result = watcher.wait(timeout=120)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    "Docker watcher cleanup timed out; outcome is incomplete"
                ) from exc
            if result != 0:
                raise RuntimeError("Docker watcher reported incomplete cleanup; inspect run status")


def migrate(url: str) -> None:
    if url not in ACTIVE_URLS:
        raise RuntimeError("migration URL is not a generated disposable lab database")
    Settings.model_config["env_file"] = None
    os.environ["KNOWLEDGE_VAULT_ENVIRONMENT"] = "test"
    os.environ["KNOWLEDGE_VAULT_DATABASE_URL"] = url
    try:
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    finally:
        del os.environ["KNOWLEDGE_VAULT_ENVIRONMENT"]
        del os.environ["KNOWLEDGE_VAULT_DATABASE_URL"]


def reset_and_verify(url: str) -> None:
    if url not in ACTIVE_URLS:
        raise RuntimeError("reset refused for a non-lab database")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
            for table in (
                "assertions",
                "temporal_evidence",
                "temporal_retirements",
                "flush_batches",
            ):
                if connection.scalar(text(f"SELECT count(*) FROM {table}")) != 0:  # noqa: S608
                    raise RuntimeError(f"reset verification failed for {table}")
    finally:
        engine.dispose()


def credentials(url: str) -> tuple[Settings, str]:
    token = secrets.token_urlsafe(48)
    pepper = secrets.token_urlsafe(48)
    digest = hmac.new(pepper.encode(), token.encode(), hashlib.sha256).hexdigest()
    Settings.model_config["env_file"] = None
    settings = Settings(
        environment="test",
        database_url=url,
        token_pepper=pepper,
        bootstrap_tokens=json.dumps(
            [
                {
                    "principal_id": "m1-lab",
                    "sha256": digest,
                    "scopes": ["knowledge:read", "knowledge:write"],
                }
            ]
        ),
        public_base_url="http://127.0.0.1",
        auth_issuer_url="http://127.0.0.1",
        embeddings_enabled=False,
        embedding_allow_download=False,
    )
    return settings, token


def database_versions(url: str) -> dict[str, str]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return {
                "schema_revision": str(
                    connection.scalar(text("SELECT version_num FROM alembic_version"))
                ),
                "postgres_version": str(connection.scalar(text("SHOW server_version"))),
            }
    finally:
        engine.dispose()


def server_commit() -> str:
    return subprocess.check_output(
        ["/usr/bin/git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def write_operator_files(output: Path, token: str, manifest: dict[str, object]) -> Path:
    owner = manifest.get("lab_owner")
    if not isinstance(owner, str) or not owned_output(output, owner):
        raise RuntimeError("output directory is not positively owned by this lab run")
    token_path = output / "lab-token"
    descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(token)
    (output / "environment.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return token_path


def cleanup_interrupted(output: Path, *, confirmed_settled: bool = False) -> None:
    if output.is_symlink() or not output.is_dir() or output.stat().st_uid != os.getuid():
        raise RuntimeError("output directory is not positively owned by this lab run")
    manifest = json.loads((output / "environment.json").read_text())
    owner = manifest.get("lab_owner")
    socket_raw = manifest.get("db_socket_dir")
    if (
        not isinstance(owner, str)
        or len(owner) != 32
        or any(c not in "0123456789abcdef" for c in owner)
    ):
        raise RuntimeError("invalid lab owner in manifest")
    if not isinstance(socket_raw, str):
        raise RuntimeError("missing lab socket directory in manifest")
    socket_dir = Path(socket_raw)
    if socket_dir.parent != Path("/tmp") or not socket_dir.name.startswith(  # noqa: S108
        f"kv-client-lab-{owner}-"
    ):
        raise RuntimeError("invalid lab socket directory in manifest")
    if not socket_dir.exists():
        descriptor = None
    else:
        descriptor = os.open(socket_dir / ".parent-lock", os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    "lab parent or watcher is active; cleanup is not yet safe"
                ) from exc
        if not owned_output(output, owner):
            raise RuntimeError("output directory is not positively owned by this lab run")
        state = read_lifecycle(output, owner)
        if state["socket_dir"] != str(socket_dir):
            raise RuntimeError("contradictory lab socket directory")
        if state["cleanup"] == "complete" and descriptor is not None:
            raise RuntimeError("completed cleanup has a remaining socket directory")
        if process_active(cast(list[int], state["parent"])) or (
            state["watcher"] is not None and process_active(cast(list[int], state["watcher"]))
        ):
            raise RuntimeError("lab parent or watcher is active; cleanup is not yet safe")
        if state["watcher"] is None and state["launch"] != "failed_safe":
            raise RuntimeError("Docker launcher identity is missing; cleanup is not yet safe")
        if descriptor is not None:
            launch_status = socket_dir / ".launch-status"
            if launch_status.exists():
                recorded = launch_status.read_text(encoding="ascii")
                if (
                    recorded not in {"started", "failed"}
                    or (recorded == "started" and state["launch"] != "started")
                    or (recorded == "failed" and state["launch"] == "started")
                ):
                    raise RuntimeError("contradictory Docker launch status")
        if state["launch"] in {"pending", "uncertain"} and not (
            state["settled_confirmed"] or confirmed_settled
        ):
            raise RuntimeError(
                "Docker launch outcome is unresolved; wait for watcher or confirm daemon settled"
            )
        cleanup_owned(owner, socket_dir, output)
        state["cleanup"] = "complete"
        if state["launch"] == "pending":
            state["launch"] = "uncertain"
        state["settled_confirmed"] = state["launch"] == "uncertain"
        write_lifecycle(output, owner, state)
        (output / ".cleanup-status").write_text("complete by manual recovery\n")
    finally:
        if descriptor is not None:
            os.close(descriptor)


class LocalServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        yield


@contextlib.asynccontextmanager
async def serve(settings: Settings):
    server = LocalServer(
        uvicorn.Config(
            create_app(build_container(settings)),
            host="127.0.0.1",
            port=0,
            log_config=None,
            access_log=False,
            proxy_headers=False,
        )
    )
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            if task.done():
                task.result()
            await asyncio.sleep(0.02)
        socket = server.servers[0].sockets[0]
        if socket.getsockname()[0] != "127.0.0.1":
            raise RuntimeError("MCP endpoint is not loopback-bound")
        yield f"http://127.0.0.1:{socket.getsockname()[1]}"
    finally:
        server.should_exit = True
        await task


async def smoke(base: str, token: str) -> dict[str, object]:
    url = local_mcp_url(base)
    async with local_http_client(timeout=10) as anonymous:
        refusal = await anonymous.post(url, json={})
        if refusal.status_code != 401:
            raise RuntimeError("anonymous MCP request was not refused")
    async with (
        local_http_client(token=token) as http,
        streamable_http_client(url, http_client=http) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        initialized = await session.initialize()
        trace = ["initialize"]
        tools = {tool.name for tool in (await session.list_tools()).tools}
        trace.append("tools/list")
        required = {
            "begin_knowledge_flush",
            "append_knowledge",
            "commit_knowledge_flush",
            "get_knowledge",
            "search_knowledge",
        }
        if not required <= tools:
            raise RuntimeError("required MCP tools missing")

        async def call(name: str, arguments: dict[str, object]) -> dict[str, Any]:
            result = await session.call_tool(name, arguments)
            if result.is_error or result.structured_content is None:
                raise RuntimeError(f"MCP tool failed: {name}")
            trace.append(name)
            return result.structured_content

        key = uuid4().hex
        begin = await call(
            "begin_knowledge_flush",
            {"idempotency_key": key, "declared_parts": 1, "declared_items": 1},
        )
        batch = begin["batch_id"]
        item = {
            "content": "Synthetic lab lantern is blue.",
            "kind": "user_fact",
            "origin": "user",
            "topics": ["synthetic-lab"],
        }
        await call("append_knowledge", {"batch_id": batch, "part_number": 1, "assertions": [item]})
        committed = await call("commit_knowledge_flush", {"batch_id": batch})
        replay = await call("commit_knowledge_flush", {"batch_id": batch})
        if (
            committed["assertion_ids"] != replay["assertion_ids"]
            or committed["counts"]["inserted"] != 1
        ):
            raise RuntimeError("idempotent commit failed")
        assertion_id = committed["assertion_ids"][0]
        readback = await call("get_knowledge", {"assertion_id": assertion_id})
        search = await call("search_knowledge", {"query": "synthetic lab lantern", "limit": 5})
        if readback["id"] != assertion_id or not search["results"]:
            raise RuntimeError("readback or retrieval failed")
        return {
            "server_name": initialized.server_info.name,
            "tool_count": len(tools),
            "anonymous_refused": True,
            "committed": 1,
            "readback": True,
            "query_results": len(search["results"]),
            "trace": trace,
        }


async def run(mode: str, output: Path | None, stop: asyncio.Event | None = None) -> None:
    stop = stop or asyncio.Event()
    if mode == "serve" and output is None:
        raise RuntimeError("serve requires an operator-local output directory")
    preflight = check_host()
    check_interruption(stop.is_set)
    with disposable_postgres(stop.is_set, output) as (url, owner, _container, socket_dir):
        migrate(url)
        check_interruption(stop.is_set)
        reset_and_verify(url)
        check_interruption(stop.is_set)
        settings, token = credentials(url)
        async with serve(settings) as base:
            check_interruption(stop.is_set)
            manifest = {
                "created_utc": datetime.now(UTC).isoformat(),
                "lab_owner": owner,
                "db_socket_dir": str(socket_dir),
                "server_commit": await asyncio.to_thread(server_commit),
                **database_versions(url),
                "postgres_image": IMAGE,
                "embedding_model": settings.embedding_model,
                "embedding_revision": None,
                "embedding_mode": "disabled infrastructure smoke",
                "endpoint": base,
                "cpu_count": os.cpu_count(),
                "host_platform": platform.platform(),
                "docker_root_free_bytes": preflight["docker_root_free_bytes"],
                "memory_available_bytes": preflight["memory_available_bytes"],
            }
            if mode == "smoke":
                result = await smoke(base, token)
                check_interruption(stop.is_set)
                reset_and_verify(url)
                print(json.dumps({"manifest": manifest, "smoke": result, "reset_verified": True}))
            else:
                if output is None:
                    raise RuntimeError("serve requires an operator-local output directory")
                token_path = output / "lab-token"
                try:
                    await asyncio.to_thread(write_operator_files, output, token, manifest)
                    check_interruption(stop.is_set)
                    print(
                        f"MCP endpoint: {base}/mcp\nToken file: {token_path}\n"
                        "Press Ctrl-C to end this run.",
                        flush=True,
                    )
                    await stop.wait()
                finally:
                    try:
                        token_path.unlink(missing_ok=True)
                    finally:
                        reset_and_verify(url)
    check_interruption(stop.is_set)


@contextlib.contextmanager
def operator_signals(stop: asyncio.Event):
    old_int = signal.getsignal(signal.SIGINT)
    old_term = signal.getsignal(signal.SIGTERM)

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preflight", "smoke", "serve", "cleanup", "watch"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--owner")
    parser.add_argument("--socket-dir", type=Path)
    parser.add_argument("--confirm-daemon-settled", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.mode == "preflight":
        print(json.dumps(check_host()))
    elif args.mode == "cleanup":
        refuse_inherited_configuration(dict(os.environ))
        if args.output is None:
            parser.error("cleanup requires --output")
        cleanup_interrupted(args.output, confirmed_settled=args.confirm_daemon_settled)
    elif args.mode == "watch":
        if args.owner is None or args.socket_dir is None:
            parser.error("internal watcher requires --owner and --socket-dir")
        watch_parent(args.owner, args.socket_dir, args.output)
    else:
        stop = asyncio.Event()
        with operator_signals(stop):
            try:
                asyncio.run(run(args.mode, args.output, stop))
            except LabInterrupted:
                print("lab interrupted; owned resources cleaned", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(f"lab failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    except subprocess.CalledProcessError as error:
        print(f"lab failed: subprocess exit {error.returncode}", file=sys.stderr)
        raise SystemExit(1) from error
