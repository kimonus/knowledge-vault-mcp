import base64
import hashlib
import hmac
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any, cast

DEFAULT_DEVICE_SCOPES = ["knowledge:read", "knowledge:write"]


def write_private_file(path: Path, value: str) -> None:
    """Create a new owner-only file without overwriting an existing credential."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(value)


def generate_record(
    principal: str, scopes: list[str], pepper: str
) -> tuple[str, dict[str, object]]:
    token = "kv_" + secrets.token_urlsafe(32)
    digest = hmac.new(pepper.encode(), token.encode(), hashlib.sha256).hexdigest()
    return token, {"principal_id": principal, "sha256": digest, "scopes": scopes}


def build_secret_patch(secret: dict[str, Any], record: dict[str, Any]) -> dict[str, object]:
    principal = record.get("principal_id")
    if not isinstance(principal, str) or not principal.strip():
        raise ValueError("record principal_id must be a non-empty string")
    digest = record.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("record sha256 must be a hexadecimal SHA-256 digest")
    try:
        if len(bytes.fromhex(digest)) != 32:
            raise ValueError
    except ValueError as exc:
        raise ValueError("record sha256 must be a hexadecimal SHA-256 digest") from exc
    scopes = record.get("scopes")
    if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
        raise ValueError("record scopes must be a list of strings")

    data = secret.get("data")
    if not isinstance(data, dict):
        raise ValueError("Secret has no data object")
    encoded_records = data.get("bootstrap-tokens")
    if not isinstance(encoded_records, str):
        raise ValueError("Secret has no data.bootstrap-tokens value")
    decoded = base64.b64decode(encoded_records, validate=True)
    untyped_records: object = json.loads(decoded)
    if not isinstance(untyped_records, list):
        raise ValueError("bootstrap-tokens must contain a JSON array")
    records = cast(list[dict[str, Any]], untyped_records)
    if any(item.get("principal_id") == principal for item in records):
        raise ValueError(f"principal already exists: {principal}")

    records.append(record)
    serialized = json.dumps(records, separators=(",", ":")).encode()
    return {
        "data": {
            "bootstrap-tokens": base64.b64encode(serialized).decode(),
        }
    }


def read_token(path: Path) -> str:
    if os.name == "posix":
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise PermissionError(f"token file must not be accessible by group or others: {path}")
    token = path.read_text(encoding="utf-8").strip()
    if not token.startswith("kv_") or any(character.isspace() for character in token):
        raise ValueError("token file does not contain a valid Knowledge Vault token")
    return token
