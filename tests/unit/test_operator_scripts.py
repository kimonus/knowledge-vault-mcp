import base64
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from knowledge_vault.auth.device_tokens import build_secret_patch, read_token


def encoded_secret(records: object) -> dict[str, object]:
    return {
        "data": {
            "bootstrap-tokens": base64.b64encode(json.dumps(records).encode()).decode(),
        }
    }


def test_generate_token_writes_private_files_without_printing_secret(tmp_path: Path) -> None:
    token_path = tmp_path / "device.token"
    record_path = tmp_path / "record.json"
    result = subprocess.run(  # noqa: S603 - fixed interpreter and repository script
        [
            sys.executable,
            "scripts/generate_token.py",
            "--principal",
            "test-device",
            "--pepper",
            "test-pepper",
            "--token-output",
            str(token_path),
            "--record-output",
            str(record_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    token = token_path.read_text(encoding="utf-8").strip()
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert token.startswith("kv_")
    assert token not in result.stdout
    assert record["principal_id"] == "test-device"
    assert record["scopes"] == ["knowledge:read", "knowledge:write"]
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(record_path.stat().st_mode) == 0o600


def test_build_patch_adds_record_without_copying_other_secret_keys() -> None:
    existing = [{"principal_id": "existing", "sha256": "a" * 64, "scopes": ["knowledge:read"]}]
    secret = {
        "data": {
            "bootstrap-tokens": base64.b64encode(json.dumps(existing).encode()).decode(),
            "token-pepper": "must-not-be-copied",
        }
    }
    record = {
        "principal_id": "new-device",
        "sha256": "b" * 64,
        "scopes": ["knowledge:read", "knowledge:write"],
    }

    patch = build_secret_patch(secret, record)
    assert set(patch["data"]) == {"bootstrap-tokens"}
    decoded = json.loads(base64.b64decode(patch["data"]["bootstrap-tokens"]))
    assert decoded == [*existing, record]


def test_build_patch_rejects_duplicate_principal() -> None:
    records = [{"principal_id": "same", "sha256": "a" * 64, "scopes": ["knowledge:read"]}]
    secret = encoded_secret(records)
    with pytest.raises(ValueError, match="principal already exists"):
        build_secret_patch(secret, records[0])


@pytest.mark.parametrize(
    ("record", "message"),
    [
        ({"principal_id": "", "sha256": "a" * 64, "scopes": []}, "principal_id"),
        ({"principal_id": "p", "sha256": "short", "scopes": []}, "sha256"),
        ({"principal_id": "p", "sha256": "z" * 64, "scopes": []}, "sha256"),
        ({"principal_id": "p", "sha256": "a" * 64, "scopes": "read"}, "scopes"),
        ({"principal_id": "p", "sha256": "a" * 64, "scopes": [1]}, "scopes"),
    ],
)
def test_build_patch_rejects_malformed_record(record: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build_secret_patch(encoded_secret([]), record)


@pytest.mark.parametrize(
    ("secret", "message"),
    [
        ({}, "data object"),
        ({"data": {}}, "bootstrap-tokens"),
        (encoded_secret({"not": "a list"}), "JSON array"),
    ],
)
def test_build_patch_rejects_malformed_secret(secret: dict[str, object], message: str) -> None:
    record = {"principal_id": "p", "sha256": "a" * 64, "scopes": ["knowledge:read"]}
    with pytest.raises(ValueError, match=message):
        build_secret_patch(secret, record)


def test_header_helper_requires_private_valid_token_file(tmp_path: Path) -> None:
    token_path = tmp_path / "device.token"
    token_path.write_text("kv_example-token\n", encoding="utf-8")
    token_path.chmod(0o600)
    assert read_token(token_path) == "kv_example-token"

    token_path.chmod(0o644)
    with pytest.raises(PermissionError):
        read_token(token_path)

    token_path.chmod(0o600)
    token_path.write_text("not-a-token\n", encoding="utf-8")
    with pytest.raises(ValueError, match="valid Knowledge Vault token"):
        read_token(token_path)
