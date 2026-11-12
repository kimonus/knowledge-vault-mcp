#!/usr/bin/env python3
"""Create fresh local-test credentials without printing their values.

This does not provision TLS or configure container user-namespace permissions.
The destination's parent must be trusted and already exist.
"""

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
from pathlib import Path


def write_private(directory_fd: int, name: str, value: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(value)


def provision(destination: Path) -> None:
    # The new directory is never reused: a partial attempt requires a new destination.
    os.mkdir(destination, 0o700)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(destination, flags)
    try:
        os.fchmod(directory_fd, 0o700)
        password = secrets.token_urlsafe(32)
        pepper = secrets.token_urlsafe(32)
        token = "kv_" + secrets.token_urlsafe(32)
        digest = hmac.new(pepper.encode(), token.encode(), hashlib.sha256).hexdigest()
        record = {
            "principal_id": "standalone-device",
            "sha256": digest,
            "scopes": ["knowledge:read", "knowledge:write"],
        }
        write_private(directory_fd, "postgres-password", password)
        write_private(
            directory_fd,
            "database-url",
            f"postgresql://knowledge_vault:{password}@db:5432/knowledge_vault",
        )
        write_private(directory_fd, "token-pepper", pepper)
        write_private(directory_fd, "bootstrap-tokens", json.dumps([record], separators=(",", ":")))
        write_private(directory_fd, "device-token", token)
    finally:
        os.close(directory_fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("directory", type=Path, help="new operator-owned directory")
    args = parser.parse_args()
    try:
        provision(args.directory)
    except OSError:
        print(
            "Provisioning failed; any partial directory and files were left untouched.",
            file=sys.stderr,
        )
        return 1
    print("Created credentials. TLS and mapped container permissions require separate setup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
