#!/usr/bin/env python3
"""Generate an opaque bearer token and its HMAC-only configuration record."""

import argparse
import json
import sys
from pathlib import Path

from knowledge_vault.auth.device_tokens import (
    DEFAULT_DEVICE_SCOPES,
    generate_record,
    write_private_file,
)
from knowledge_vault.auth.tokens import Scope


def read_pepper(args: argparse.Namespace) -> str:
    if args.pepper_file is not None:
        return Path(args.pepper_file).read_text(encoding="utf-8").strip()
    return sys.stdin.read().strip()


def main() -> None:
    # Abbreviated options are disabled so a mistyped flag fails instead of matching another one.
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--principal", "--principal-id", dest="principal", default="me")
    # The pepper is read from a file or standard input only: command-line arguments are visible
    # to other local users and are kept in shell history.
    pepper_source = parser.add_mutually_exclusive_group(required=True)
    pepper_source.add_argument("--pepper-file", type=Path)
    pepper_source.add_argument("--pepper-stdin", action="store_true")
    parser.add_argument(
        "--scopes",
        "--scope",
        dest="scopes",
        nargs="+",
        action="extend",
        choices=[scope.value for scope in Scope],
        help="may be repeated; defaults to read and write",
    )
    parser.add_argument("--token-output", type=Path)
    parser.add_argument("--record-output", type=Path)
    args = parser.parse_args()
    pepper = read_pepper(args)
    if not pepper:
        parser.error("pepper cannot be empty")

    scopes = list(dict.fromkeys(args.scopes or DEFAULT_DEVICE_SCOPES))
    token, record = generate_record(args.principal, scopes, pepper)
    record_json = json.dumps(record, separators=(",", ":"))

    if args.token_output is None:
        print(f"TOKEN={token}")
    else:
        write_private_file(args.token_output, token + "\n")
        print(f"TOKEN_FILE={args.token_output}")

    if args.record_output is None:
        print("RECORD=" + record_json)
    else:
        write_private_file(args.record_output, record_json + "\n")
        print(f"RECORD_FILE={args.record_output}")


if __name__ == "__main__":
    main()
