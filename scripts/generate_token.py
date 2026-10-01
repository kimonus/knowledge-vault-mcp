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


def read_pepper(args: argparse.Namespace) -> str:
    if args.pepper is not None:
        return str(args.pepper)
    if args.pepper_file is not None:
        return Path(args.pepper_file).read_text(encoding="utf-8").strip()
    return sys.stdin.read().strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--principal", default="me")
    pepper_source = parser.add_mutually_exclusive_group(required=True)
    pepper_source.add_argument("--pepper")
    pepper_source.add_argument("--pepper-file", type=Path)
    pepper_source.add_argument("--pepper-stdin", action="store_true")
    parser.add_argument(
        "--scopes",
        nargs="+",
        default=DEFAULT_DEVICE_SCOPES,
    )
    parser.add_argument("--token-output", type=Path)
    parser.add_argument("--record-output", type=Path)
    args = parser.parse_args()
    pepper = read_pepper(args)
    if not pepper:
        parser.error("pepper cannot be empty")

    token, record = generate_record(args.principal, args.scopes, pepper)
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
