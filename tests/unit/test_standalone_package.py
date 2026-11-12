"""Static checks only; real Compose and gateway behavior needs runtime tests."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "deploy/standalone"


def test_compose_security_boundaries():
    compose = yaml.safe_load((PACKAGE / "compose.yaml").read_text())
    services = compose["services"]
    assert {name for name, service in services.items() if "ports" in service} == {"gateway"}
    assert services["gateway"]["ports"] == ["127.0.0.1:8443:8443"]
    assert compose["networks"]["private"]["internal"] is True
    assert services["gateway"]["networks"] == ["private", "frontend"]
    assert all(services[name]["networks"] == ["private"] for name in ("api", "worker", "db"))
    assert services["migrate"]["profiles"] == ["migration"]
    assert services["migrate"]["entrypoint"] == ["knowledge-vault-migrate"]
    assert services["worker"]["entrypoint"] == ["knowledge-vault-worker"]
    assert services["api"]["entrypoint"] == ["knowledge-vault-api"]
    for name in ("api", "worker", "migrate"):
        env = services[name]["environment"]
        assert env["KNOWLEDGE_VAULT_STANDALONE_MODE"] == "true"
        assert env["KNOWLEDGE_VAULT_EMBEDDINGS_ENABLED"] == "false"
        assert env["KNOWLEDGE_VAULT_DATABASE_URL_FILE"] == "/run/kv-secrets/database-url"
        assert services[name]["read_only"] is True
    assert services["db"]["image"].endswith(
        "@sha256:137f044b0efe3d57f39b972b9b53641b1f2045b99d879e298bbf514a25787dcf"
    )


def test_gateway_is_allowlisted_and_tls_only():
    config = (PACKAGE / "nginx.conf").read_text()
    assert "listen 8443 ssl;" in config
    assert "proxy_set_header Host $http_host;" in config
    assert "proxy_read_timeout 3600s;" in config
    assert "proxy_buffering off;" in config
    assert "location = /mcp" in config
    assert "location ^~ /api/v1/" in config
    assert "location / { return 404; }" in config
    assert "/metrics" not in config
    assert "/health" not in config


def test_configuration_checker_fails_closed(tmp_path):
    env = os.environ | {
        "KV_APP_IMAGE": "registry.example/app:latest",
        "KV_GATEWAY_IMAGE": "registry.example/proxy@sha256:" + "a" * 64,
        "KV_SECRETS_DIR": str(tmp_path),
    }
    command = ["sh", str(PACKAGE / "check-config.sh")]
    assert subprocess.run(command, env=env, capture_output=True, check=False).returncode != 0  # noqa: S603
    env["KV_APP_IMAGE"] = "registry.example/app@sha256:" + "b" * 64
    assert subprocess.run(command, env=env, capture_output=True, check=False).returncode != 0  # noqa: S603
    for name in (
        "database-url",
        "token-pepper",
        "bootstrap-tokens",
        "postgres-password",
        "tls-cert.pem",
        "tls-key.pem",
    ):
        (tmp_path / name).write_text("placeholder")
    assert subprocess.run(command, env=env, capture_output=True, check=False).returncode == 0  # noqa: S603


def test_database_wait_accepts_healthy_and_rejects_unhealthy(tmp_path):
    engine = tmp_path / "docker"
    engine.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = compose ]; then echo disposable-id; '
        "else printf '%s\\n' \"$TEST_STATE\"; fi\n"
    )
    engine.chmod(0o755)
    env = os.environ | {"PATH": str(tmp_path), "TEST_STATE": "running healthy"}
    command = ["/bin/sh", str(PACKAGE / "wait-db.sh")]
    assert subprocess.run(command, env=env, capture_output=True, check=False).returncode == 0  # noqa: S603
    env["TEST_STATE"] = "running unhealthy"
    assert subprocess.run(command, env=env, capture_output=True, check=False).returncode != 0  # noqa: S603
    env["TEST_STATE"] = "exited unhealthy"
    assert subprocess.run(command, env=env, capture_output=True, check=False).returncode != 0  # noqa: S603


@pytest.mark.parametrize(
    "symlink_name",
    [
        "database-url",
        "token-pepper",
        "bootstrap-tokens",
        "postgres-password",
        "tls-cert.pem",
        "tls-key.pem",
    ],
)
def test_configuration_checker_rejects_symlink_files(tmp_path, symlink_name):
    target = tmp_path / "target"
    target.write_text("placeholder")
    for name in (
        "database-url",
        "token-pepper",
        "bootstrap-tokens",
        "postgres-password",
        "tls-cert.pem",
        "tls-key.pem",
    ):
        path = tmp_path / name
        if name == symlink_name:
            path.symlink_to(target)
        else:
            path.write_text("placeholder")
    env = os.environ | {
        "KV_APP_IMAGE": "registry.example/app@sha256:" + "b" * 64,
        "KV_GATEWAY_IMAGE": "registry.example/proxy@sha256:" + "a" * 64,
        "KV_SECRETS_DIR": str(tmp_path),
    }
    result = subprocess.run(  # noqa: S603
        ["/bin/sh", str(PACKAGE / "check-config.sh")], env=env, capture_output=True, check=False
    )
    assert result.returncode != 0
    assert b"must not be a symlink" in result.stderr
