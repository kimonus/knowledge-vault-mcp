"""Opt-in real Docker lifecycle, encrypted recovery and failed-upgrade rehearsal.

Only generated credentials and synthetic assertions are used. The normal database
integration suite remains independently mandatory. Failed fixtures are retained.
"""

import asyncio
import json
import os
import secrets
import signal
import ssl
import subprocess
import sys
import time
from pathlib import Path

import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "deploy/standalone/docker.py"
pytestmark = pytest.mark.skipif(
    not os.environ.get("KV_DOCKER_TEST_APP_IMAGE"), reason="opt-in Docker lifecycle images required"
)


def credentials(state):
    config = json.loads((state / "config.json").read_text())
    return state / "credentials" / config["credential_generation"]


def run(*args, env=None, success=True, timeout=600):
    result = subprocess.run(  # noqa: S603
        [str(value) for value in args], env=env, capture_output=True, timeout=timeout, check=False
    )
    if success:
        assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    else:
        assert result.returncode != 0
    return result.stdout.decode()


def test_local_docker_lifecycle_and_fresh_target_recovery(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    source, target = tmp_path / "source", tmp_path / "restored"
    repository, key = tmp_path / "repository", tmp_path / "restic-key"
    repository.mkdir(mode=0o700)
    key.write_text(secrets.token_urlsafe(32))
    key.chmod(0o600)
    images = [
        "--app-image",
        os.environ["KV_DOCKER_TEST_APP_IMAGE"],
        "--backup-image",
        os.environ["KV_DOCKER_TEST_BACKUP_IMAGE"],
    ]
    port = int(os.environ.get("KV_DOCKER_TEST_PORT", "18443"))

    def control(state, *args, success=True):
        return run(sys.executable, CONTROLLER, "--state", state, *args, success=success)

    def fixture(state, *, verify=False):
        config = json.loads((state / "config.json").read_text())
        env = os.environ | {
            "KV_TEST_SECRETS_DIR": str(credentials(state)),
            "KV_RECOVERY_TOKEN_FILE": "device-token",
            "KV_RECOVERY_STATE": str(tmp_path / "fixture.json"),
            "KV_RECOVERY_URL": f"https://localhost:{config['port']}/mcp",
        }
        if verify:
            env["KV_RECOVERY_VERIFY"] = "1"
        run(sys.executable, ROOT / "scripts/ci/standalone_recovery_client.py", env=env)

    control(source, "init", *images, "--port", port)
    control(source, "start")
    fixture(source)
    control(source, "stop")
    control(source, "start")
    fixture(source, verify=True)
    control(source, "recreate")
    fixture(source, verify=True)
    old_token = (credentials(source) / "device-token").read_text()

    def token_status(token):
        tls = ssl.create_default_context(cafile=str(credentials(source) / "tls-cert.pem"))
        with httpx2.Client(verify=tls, trust_env=False) as client:
            return client.get(
                f"https://localhost:{port}/api/v1/statistics",
                headers={"Authorization": "Bearer " + token},
            ).status_code

    control(source, "rotate-token")
    fixture(source, verify=True)
    current_token = (credentials(source) / "device-token").read_text()
    assert token_status(old_token) == 200
    # Kill the real controller after durable retirement selection but before restart.
    # Recovery must use that generation and never reactivate the overlapping token.
    interrupted = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-c",
            """
import importlib.util, os, signal, sys
spec = importlib.util.spec_from_file_location('interrupted_controller', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
phase = module.Instance.phase
def killed(self, name, **metadata):
    phase(self, name, **metadata)
    if name == 'credentials-selected':
        os.kill(os.getpid(), signal.SIGKILL)
module.Instance.phase = killed
sys.argv = ['docker.py', '--state', sys.argv[2], 'rotate-token', '--retire-previous']
module.main()
""",
            str(CONTROLLER),
            str(source),
        ],
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert interrupted.returncode == -signal.SIGKILL
    selected = json.loads((source / "config.json").read_bytes())["credential_generation"]
    control(source, "verify", success=False)
    control(source, "recover")
    assert json.loads((source / "config.json").read_bytes())["credential_generation"] == selected
    fixture(source, verify=True)
    assert (credentials(source) / "device-token").read_text() == current_token
    assert token_status(old_token) == 401
    assert token_status(current_token) == 200
    output = control(
        source, "backup", "--init-repository", "--repository", repository, "--password-file", key
    )
    snapshot = output.strip().split()[-1]
    assert len(snapshot) == 64
    control(
        source,
        "upgrade",
        "--app-image",
        os.environ["KV_DOCKER_TEST_APP_IMAGE"],
        "--repository",
        repository,
        "--password-file",
        key,
    )
    fixture(source, verify=True)
    # An acknowledged write during the upgrade, just before its stop boundary,
    # must be in the NEW recovery snapshot, not merely an earlier online backup.
    config = json.loads((source / "config.json").read_text())
    import importlib.util

    spec = importlib.util.spec_from_file_location("lifecycle_controller", CONTROLLER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    instance = module.Instance(source, config)
    compose, quiesce = instance.compose, instance.quiesce
    acknowledged = []

    def boundary():
        acknowledged.append(asyncio.run(late_write(source)))
        quiesce()

    def fail_migration(*args, **kwargs):
        if "migrate" in args:
            run(
                "docker",
                "exec",
                instance.container("db"),
                "psql",
                "-U",
                "knowledge_vault",
                "-d",
                "knowledge_vault",
                "--no-psqlrc",
                "--set=ON_ERROR_STOP=1",
                "-c",
                "UPDATE alembic_version SET version_num='failed_upgrade'",
            )
            raise module.Failed("injected partial migration")
        return compose(*args, **kwargs)

    monkeypatch.setattr(instance, "quiesce", boundary)
    monkeypatch.setattr(instance, "compose", fail_migration)
    with module.lifecycle_lock(source), pytest.raises(module.Failed, match="partial migration"):
        instance.upgrade(config["app_image"], repository, key)
    assert acknowledged
    snapshot = json.loads((source / "config.json").read_bytes())["pre_upgrade_snapshot"]
    control(source, "start", success=False)  # interrupted operation cannot masquerade as ready
    control(source, "recover", success=False)  # possible migration requires fresh-target restore
    assert instance.inspect("container", instance.container("api"))["State"]["Status"] == "exited"
    control(
        target,
        "restore",
        *images,
        "--port",
        port + 1,
        "--repository",
        repository,
        "--password-file",
        key,
        "--snapshot",
        snapshot,
    )
    fixture(target, verify=True)
    assert asyncio.run(late_write(target, assertion_id=acknowledged[0])) == acknowledged[0]
    recovered = json.loads((target / "config.json").read_text())
    assert config["database_volume"] != recovered["database_volume"]
    assert config["credential_volume"] != recovered["credential_volume"]
    assert (credentials(source) / "token-pepper").read_bytes() == (
        credentials(target) / "token-pepper"
    ).read_bytes()
    assert (credentials(source) / "device-token").read_bytes() != (
        credentials(target) / "device-token"
    ).read_bytes()
    # Explicit refusal to reuse either state directory; no rollback over the source.
    control(
        source,
        "restore",
        *images,
        "--repository",
        repository,
        "--password-file",
        key,
        "--snapshot",
        snapshot,
        success=False,
    )
    control(source, "stop")
    control(target, "stop")


async def late_write(state, *, assertion_id=None, key="late-upgrade-write", missing=False):
    config = json.loads((state / "config.json").read_text())
    content = (
        "A synthetic write was acknowledged before upgrade quiescence."
        if key == "late-upgrade-write"
        else f"A synthetic upgrade-boundary write was acknowledged for {key}."
    )
    tls = ssl.create_default_context(cafile=str(credentials(state) / "tls-cert.pem"))
    async with (
        httpx2.AsyncClient(
            verify=tls,
            headers={
                "Authorization": "Bearer " + (credentials(state) / "device-token").read_text(),
            },
            timeout=30,
            trust_env=False,
        ) as http,
        streamable_http_client(
            f"https://localhost:{config['port']}/mcp", http_client=http
        ) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()

        async def tool(name, args):
            result = await session.call_tool(name, args)
            assert not result.is_error and result.structured_content is not None
            return result.structured_content

        if assertion_id:
            if missing:
                result = await session.call_tool("get_knowledge", {"assertion_id": assertion_id})
                assert result.is_error
                assert any("not_found" in getattr(item, "text", "") for item in result.content)
                return None
            item = await tool("get_knowledge", {"assertion_id": assertion_id})
            assert item["content"] == content
            return assertion_id
        batch = await tool(
            "begin_knowledge_flush",
            {
                "idempotency_key": key,
                "declared_parts": 1,
                "declared_items": 1,
            },
        )
        await tool(
            "append_knowledge",
            {
                "batch_id": batch["batch_id"],
                "part_number": 1,
                "assertions": [
                    {
                        "content": content,
                        "kind": "configuration",
                        "origin": "user",
                    }
                ],
            },
        )
        result = await tool("commit_knowledge_flush", {"batch_id": batch["batch_id"]})
        return result["assertion_ids"][0]


async def full_client(state, *, warm=False):
    config = json.loads((state / "config.json").read_text())
    tls = ssl.create_default_context(cafile=str(credentials(state) / "tls-cert.pem"))
    token = (credentials(state) / "device-token").read_text()
    async with (
        httpx2.AsyncClient(
            verify=tls, headers={"Authorization": "Bearer " + token}, timeout=90, trust_env=False
        ) as http,
        streamable_http_client(
            f"https://localhost:{config['port']}/mcp", http_client=http
        ) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()

        async def tool(name, arguments):
            result = await session.call_tool(name, arguments)
            assert not result.is_error, name
            assert result.structured_content is not None
            return result.structured_content

        batch = await tool(
            "begin_knowledge_flush",
            {"idempotency_key": "full-profile", "declared_parts": 1, "declared_items": 1},
        )
        if not warm:
            await tool(
                "append_knowledge",
                {
                    "batch_id": batch["batch_id"],
                    "part_number": 1,
                    "assertions": [
                        {
                            "content": "The synthetic fixture uses a network router.",
                            "kind": "configuration",
                            "origin": "user",
                        }
                    ],
                },
            )
        commit = await tool("commit_knowledge_flush", {"batch_id": batch["batch_id"]})
        deadline = time.monotonic() + 180
        while True:
            item = await tool("get_knowledge", {"assertion_id": commit["assertion_ids"][0]})
            if item["embedding_state"] == "ready":
                break
            assert time.monotonic() < deadline, "worker did not finish offline embedding"
            await asyncio.sleep(2)
        # First API model use: concurrent requests must share one load and serialize encoding.
        results = await asyncio.gather(
            *(
                tool("search_knowledge", {"query": "сетевое устройство", "limit": 5})
                for _ in range(2)
            )
        )
        assert all(page["results"] for page in results)
        statistics = await tool("get_knowledge_statistics", {})
        assert statistics["operations"]["worker_heartbeat_age_seconds"] is not None
        # Missing credentials, browser Origin, wrong Host and hidden operational routes.
        url = f"https://localhost:{config['port']}"
        assert (
            await http.get(url + "/api/v1/statistics", headers={"Host": "evil.example"})
        ).status_code == 400
        assert (
            await http.post(url + "/mcp", headers={"Origin": "https://evil.example"}, json={})
        ).status_code == 403
        assert (await http.get(url + "/metrics")).status_code == 404
        async with httpx2.AsyncClient(verify=tls, trust_env=False) as anonymous:
            assert (await anonymous.get(url + "/api/v1/statistics")).status_code == 401


@pytest.mark.skipif(
    not os.environ.get("KV_DOCKER_TEST_MODEL_DIR"), reason="provisioned offline model required"
)
def test_full_offline_profile_concurrent_first_use_and_network_isolation(tmp_path):
    tmp_path.chmod(0o700)
    state = tmp_path / "full"
    port = int(os.environ.get("KV_DOCKER_TEST_PORT", "18443")) + 2
    command = [sys.executable, CONTROLLER, "--state", state]
    run(
        *command,
        "init",
        "--app-image",
        os.environ["KV_DOCKER_TEST_APP_IMAGE"],
        "--backup-image",
        os.environ["KV_DOCKER_TEST_BACKUP_IMAGE"],
        "--port",
        port,
        "--model-dir",
        os.environ["KV_DOCKER_TEST_MODEL_DIR"],
        "--model-manifest",
        os.environ["KV_DOCKER_TEST_MODEL_MANIFEST"],
    )
    run(*command, "start")
    asyncio.run(full_client(state))
    run(*command, "reembed")
    asyncio.run(full_client(state, warm=True))
    config = json.loads((state / "config.json").read_text())
    identifiers = {}
    for service in ("api", "worker", "db"):
        identifiers[service] = run(
            "docker",
            "ps",
            "-q",
            "--no-trunc",
            "--filter",
            "label=com.docker.compose.project=" + config["project"],
            "--filter",
            "label=com.docker.compose.service=" + service,
        ).strip()
        info = json.loads(run("docker", "inspect", identifiers[service]))[0]
        assert not info["HostConfig"]["PortBindings"]
        assert info["RestartCount"] == 0
        logs = run("docker", "logs", identifiers[service])
        assert "The synthetic fixture uses" not in logs
        assert (credentials(state) / "device-token").read_text() not in logs
    addresses = [
        run(
            "docker",
            "inspect",
            "--format",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            identifiers[name],
        ).strip()
        for name in ("api", "db")
    ]
    run(
        "docker",
        "run",
        "--rm",
        "--network",
        "bridge",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--mount",
        f"type=bind,src={ROOT / 'scripts/ci/standalone_negative_reachability.py'},"
        "dst=/run/check.py,readonly",
        "--entrypoint",
        "python",
        config["app_image"],
        "/run/check.py",
        *addresses,
        port,
    )
    run(*command, "verify")
    run(*command, "stop")
