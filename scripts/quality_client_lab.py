#!/usr/bin/env python3
"""Disposable, loopback-only infrastructure for operator-run MCP client evaluation."""

import argparse
import asyncio
import contextlib
import hashlib
import hmac
import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
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


def refuse_inherited_configuration(environ: dict[str, str]) -> None:
    forbidden = sorted(
        key
        for key in environ
        if key.startswith(("KNOWLEDGE_VAULT_", "PG", "HF_", "HUGGINGFACE_"))
        or key in {"DATABASE_URL", "DOCKER_HOST", "DOCKER_CONTEXT", "CODEX_HOME", "MCP_CONFIG"}
    )
    if forbidden:
        raise RuntimeError("unsafe inherited configuration: " + ", ".join(forbidden))


def docker(*args: str) -> str:
    # Pin the local socket; never follow a caller's Docker context or remote daemon.
    result = subprocess.run(  # noqa: S603  # Fixed executable, harness-generated arguments.
        ["/usr/bin/docker", "-H", "unix:///var/run/docker.sock", *args],
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
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


@contextlib.contextmanager
def disposable_postgres():
    owner = uuid4().hex
    container = f"kv-client-lab-db-{owner}"
    password = secrets.token_urlsafe(32)
    socket_dir = Path(tempfile.mkdtemp(prefix=f"kv-client-lab-{owner}-", dir="/tmp"))
    socket_dir.chmod(0o700)
    url = f"postgresql+psycopg://lab:{quote(password)}@/lab?host={quote(str(socket_dir), safe='/')}"
    try:
        docker(
            "run",
            "-d",
            "--name",
            container,
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
        for _ in range(60):
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
            )
            if probe.returncode == 0:
                break
            import time

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
        if owned_resource("container", container, owner):
            docker("rm", "-f", "-v", container)
        shutil.rmtree(socket_dir)


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
    if output.resolve().is_relative_to(ROOT):
        raise RuntimeError("output directory must be outside the repository")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    token_path = output / "lab-token"
    descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(token)
    (output / "environment.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return token_path


def cleanup_interrupted(output: Path) -> None:
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
    container = f"kv-client-lab-db-{owner}"
    if owned_resource("container", container, owner):
        docker("rm", "-f", "-v", container)
    elif docker("ps", "-a", "--filter", f"name=^{container}$", "--format", "{{.Names}}"):
        raise RuntimeError("container name exists without expected owner label")
    (output / "lab-token").unlink(missing_ok=True)
    if socket_dir.exists() and socket_dir.stat().st_uid == os.getuid():
        shutil.rmtree(socket_dir)


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
    async with httpx2.AsyncClient(timeout=10) as anonymous:
        refusal = await anonymous.post(f"{base}/mcp", json={})
        if refusal.status_code != 401:
            raise RuntimeError("anonymous MCP request was not refused")
    async with (
        httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30) as http,
        streamable_http_client(f"{base}/mcp", http_client=http) as streams,
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


async def run(mode: str, output: Path | None) -> None:
    preflight = check_host()
    with disposable_postgres() as (url, owner, _container, socket_dir):
        migrate(url)
        reset_and_verify(url)
        settings, token = credentials(url)
        async with serve(settings) as base:
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
                reset_and_verify(url)
                print(json.dumps({"manifest": manifest, "smoke": result, "reset_verified": True}))
            else:
                if output is None:
                    raise RuntimeError("serve requires an operator-local output directory")
                token_path = output / "lab-token"
                try:
                    await asyncio.to_thread(write_operator_files, output, token, manifest)
                    print(
                        f"MCP endpoint: {base}/mcp\nToken file: {token_path}\n"
                        "Press Ctrl-C to end this run.",
                        flush=True,
                    )
                    await asyncio.Event().wait()
                finally:
                    token_path.unlink(missing_ok=True)
                    reset_and_verify(url)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preflight", "smoke", "serve", "cleanup"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.mode == "preflight":
        print(json.dumps(check_host()))
    elif args.mode == "cleanup":
        refuse_inherited_configuration(dict(os.environ))
        if args.output is None:
            parser.error("cleanup requires --output")
        cleanup_interrupted(args.output)
    else:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(run(args.mode, args.output))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(f"lab failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    except subprocess.CalledProcessError as error:
        print(f"lab failed: subprocess exit {error.returncode}", file=sys.stderr)
        raise SystemExit(1) from error
