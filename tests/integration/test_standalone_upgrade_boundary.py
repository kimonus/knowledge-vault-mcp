"""R-019: real controller death, TLS/MCP acknowledgements, and recovery boundaries."""

import asyncio
import contextlib
import hashlib
import json
import os
import secrets
import select
import signal
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import httpx2
import pytest
from test_standalone_docker_lifecycle import CONTROLLER, credentials, late_write, run

pytestmark = pytest.mark.skipif(
    not os.environ.get("KV_DOCKER_TEST_APP_IMAGE"), reason="opt-in Docker images required"
)

# Hooks pause AFTER real subprocess completion; the parent alone sends SIGKILL.
# No secret values are passed in argv or emitted through either synchronization pipe.
HARNESS = """
import importlib.util, os, sys
sys.dont_write_bytecode = True
controller, state, repository, key, image, boundary, notify, release = sys.argv[1:]
notify, release = int(notify), int(release)
spec = importlib.util.spec_from_file_location('upgrade_controller', controller)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
phase, compose, verify = module.Instance.phase, module.Instance.compose, module.Instance.verify
def pause():
    os.write(notify, b'B')
    os.read(release, 1)
def phase_hook(self, name, **metadata):
    phase(self, name, **metadata)
    if boundary == 'before-gateway' and name == 'starting-gateway':
        pause()
def compose_hook(self, *args, **kwargs):
    result = compose(self, *args, **kwargs)
    if boundary == 'migration-return' and 'migrate' in args:
        pause()
    if boundary == 'gateway-return' and args == ('restart', 'gateway'):
        pause()
    return result
def verify_hook(self, **kwargs):
    result = verify(self, **kwargs)
    if boundary == 'verified':
        pause()
    return result
module.Instance.phase, module.Instance.compose, module.Instance.verify = (
    phase_hook, compose_hook, verify_hook)
sys.argv = ['docker.py', '--state', state, 'upgrade', '--app-image', image,
            '--repository', repository, '--password-file', key]
raise SystemExit(module.main())
"""


@contextlib.contextmanager
def paused_upgrade(controller, state, repository, key, boundary):
    notify_read, notify_write = os.pipe()
    release_read, release_write = os.pipe()
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-c",
            HARNESS,
            str(controller),
            str(state),
            str(repository),
            str(key),
            os.environ["KV_DOCKER_TEST_APP_IMAGE"],
            boundary,
            str(notify_write),
            str(release_read),
        ],
        pass_fds=(notify_write, release_read),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    os.close(notify_write)
    os.close(release_read)
    try:
        ready, _, _ = select.select([notify_read], [], [], 240)
        assert ready and os.read(notify_read, 1) == b"B", "controller missed fault boundary"
        assert process.poll() is None
        yield
        process.kill()
        process.communicate(timeout=30)
        assert process.returncode == -signal.SIGKILL
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=30)
        os.close(notify_read)
        os.close(release_write)


def control(state, *args, controller=CONTROLLER, success=True):
    return run(sys.executable, controller, "--state", state, *args, success=success)


def prepare(tmp_path, port, controller=CONTROLLER):
    tmp_path.chmod(0o700)
    state, repository, key = tmp_path / "source", tmp_path / "repository", tmp_path / "key"
    repository.mkdir(mode=0o700)
    key.write_text(secrets.token_urlsafe(32))
    key.chmod(0o600)
    images = [
        "--app-image",
        os.environ["KV_DOCKER_TEST_APP_IMAGE"],
        "--backup-image",
        os.environ["KV_DOCKER_TEST_BACKUP_IMAGE"],
    ]
    control(state, "init", *images, "--port", port, controller=controller)
    control(state, "start", controller=controller)
    control(
        state,
        "backup",
        "--init-repository",
        "--repository",
        repository,
        "--password-file",
        key,
        controller=controller,
    )
    return state, repository, key, images


def wait_gateway(state):
    config = json.loads((state / "config.json").read_text())
    ca = ssl.create_default_context(cafile=str(credentials(state) / "tls-cert.pem"))
    token = (credentials(state) / "device-token").read_text()
    deadline = time.monotonic() + 30
    with httpx2.Client(verify=ca, trust_env=False, timeout=2) as client:
        while time.monotonic() < deadline:
            try:
                if (
                    client.get(
                        f"https://localhost:{config['port']}/api/v1/statistics",
                        headers={"Authorization": "Bearer " + token},
                    ).status_code
                    == 200
                ):
                    return
            except httpx2.TransportError:
                pass
            time.sleep(0.1)
    pytest.fail("synthetic gateway did not accept authenticated TLS")


def assert_gateway_closed(state):
    port = json.loads((state / "config.json").read_text())["port"]
    with socket.socket() as client:
        client.settimeout(2)
        assert client.connect_ex(("127.0.0.1", port)) != 0


@pytest.mark.skipif(
    not os.environ.get("KV_DOCKER_REVIEWED_CONTROLLER"),
    reason="exact captured r2 controller required for reproduction",
)
def test_exact_r2_loses_acknowledged_candidate_write_on_snapshot_recovery(tmp_path):
    reviewed = Path(os.environ["KV_DOCKER_REVIEWED_CONTROLLER"])
    port = int(os.environ.get("KV_DOCKER_TEST_PORT", "18443")) + 4
    state, repository, key, images = prepare(tmp_path, port, reviewed)
    with paused_upgrade(reviewed, state, repository, key, "gateway-return"):
        wait_gateway(state)
        identifier = asyncio.run(late_write(state, key="r2-reproduction"))
    config = json.loads((state / "config.json").read_text())
    assert config["state"] == "upgrade-in-progress"
    assert config["operation"]["migration_started"]
    assert not config["operation"].get("migration_completed")
    # R3 never advises rollback for this ambiguous legacy journal.
    for controller, diagnostic in (
        (CONTROLLER, b"Legacy interrupted upgrade may have accepted writes"),
        (reviewed, b"restore pre_upgrade_snapshot"),
    ):
        refusal = subprocess.run(  # noqa: S603
            [sys.executable, str(controller), "--state", str(state), "recover"],
            capture_output=True,
            check=False,
            timeout=120,
        )
        assert refusal.returncode == 1 and diagnostic in refusal.stderr
    target = tmp_path / "restored"
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
        config["pre_upgrade_snapshot"],
        controller=reviewed,
    )
    assert (
        asyncio.run(
            late_write(target, assertion_id=identifier, key="r2-reproduction", missing=True)
        )
        is None
    )
    (tmp_path / "boundary-evidence.json").write_text(
        json.dumps(
            {
                "scenario": "exact-r2-reproduction",
                "controller_sha256": hashlib.sha256(reviewed.read_bytes()).hexdigest(),
                "acknowledged_before_sigkill": True,
                "missing_after_r2_recommended_restore": True,
                "r3_refuses_legacy_snapshot_advice": True,
            },
            indent=2,
        )
    )
    control(state, "stop", controller=reviewed)
    control(target, "stop", controller=reviewed)


def test_upgrade_recovery_preserves_candidate_writes_at_every_exposure_boundary(tmp_path):
    port = int(os.environ.get("KV_DOCKER_TEST_PORT", "18443")) + 6
    state, repository, key, images = prepare(tmp_path, port)
    acknowledged = {"before-upgrades": asyncio.run(late_write(state, key="before-upgrades"))}
    evidence = []
    original_volume = json.loads((state / "config.json").read_text())["database_volume"]
    for boundary in ("before-gateway", "gateway-return", "verified", "migration-return"):
        accepts = boundary in ("gateway-return", "verified")
        with paused_upgrade(CONTROLLER, state, repository, key, boundary):
            if accepts:
                wait_gateway(state)
                acknowledged[boundary] = asyncio.run(late_write(state, key=boundary))
            else:
                assert_gateway_closed(state)
        config = json.loads((state / "config.json").read_text())
        assert config["state"] == "upgrade-in-progress"
        assert config["operation"]["upgrade_recovery"] == "forward-v1"
        assert bool(config["operation"].get("migration_completed")) is (
            boundary != "migration-return"
        )
        control(state, "start", success=False)
        if boundary == "migration-return":
            control(state, "recover", success=False)
            target = tmp_path / "restored"
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
                config["pre_upgrade_snapshot"],
            )
            control(state, "stop")
            state = target
        else:
            control(state, "recover")
            recovered = json.loads((state / "config.json").read_text())
            assert recovered["database_volume"] == original_volume
            assert recovered["app_image"] == config["app_image"]
        for name, identifier in acknowledged.items():
            assert asyncio.run(late_write(state, assertion_id=identifier, key=name)) == identifier
        evidence.append(
            {
                "boundary": boundary,
                "external_write_accepted": accepts,
                "acknowledged_assertions_preserved": len(acknowledged),
                "recovery": "fresh-target-no-exposure"
                if boundary == "migration-return"
                else "forward-only",
            }
        )
    (tmp_path / "boundary-evidence.json").write_text(json.dumps(evidence, indent=2))
    control(state, "stop")
