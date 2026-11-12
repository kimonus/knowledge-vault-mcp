"""Local secret-provisioning tests using temporary, disposable files only."""

import hashlib
import hmac
import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/standalone/provision-secrets.py"


def test_provision_creates_private_consistent_credentials_without_output(tmp_path):
    dest = tmp_path / "fresh"
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), str(dest)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert stat.S_IMODE(dest.stat().st_mode) == 0o700
    expected = {
        "postgres-password",
        "database-url",
        "token-pepper",
        "bootstrap-tokens",
        "device-token",
    }
    assert {path.name for path in dest.iterdir()} == expected
    for path in dest.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.read_text() not in result.stdout + result.stderr
    password = (dest / "postgres-password").read_text()
    url = urlsplit((dest / "database-url").read_text())
    assert (url.password, url.hostname, url.username, url.path) == (
        password,
        "db",
        "knowledge_vault",
        "/knowledge_vault",
    )
    token = (dest / "device-token").read_text()
    pepper = (dest / "token-pepper").read_text()
    record = json.loads((dest / "bootstrap-tokens").read_text())[0]
    assert record["sha256"] == hmac.new(pepper.encode(), token.encode(), hashlib.sha256).hexdigest()
    assert record["scopes"] == ["knowledge:read", "knowledge:write"]
    assert token not in (dest / "bootstrap-tokens").read_text()
    before = {path.name: path.read_bytes() for path in dest.iterdir()}
    repeated = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), str(dest)], capture_output=True, text=True, check=False
    )
    assert repeated.returncode != 0
    assert {path.name: path.read_bytes() for path in dest.iterdir()} == before


def test_partial_failure_is_not_removed_or_overwritten(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("standalone_provision", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = module.write_private

    def fail_on_second(directory_fd, name, value):
        if name == "database-url":
            raise OSError("simulated failure")
        original(directory_fd, name, value)

    monkeypatch.setattr(module, "write_private", fail_on_second)
    dest = tmp_path / "partial"
    with pytest.raises(OSError):
        module.provision(dest)
    before = (dest / "postgres-password").read_bytes()
    with pytest.raises(FileExistsError):
        module.provision(dest)
    assert (dest / "postgres-password").read_bytes() == before
