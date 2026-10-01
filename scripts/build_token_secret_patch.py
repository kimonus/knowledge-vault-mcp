#!/usr/bin/env python3
"""Build a Kubernetes Secret merge patch that adds one bearer-token digest record."""

import argparse
import json
import sys
from pathlib import Path

from knowledge_vault.auth.device_tokens import build_secret_patch


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record-file", type=Path, required=True)
    args = parser.parse_args()
    secret = json.load(sys.stdin)
    record = json.loads(args.record_file.read_text(encoding="utf-8"))
    json.dump(build_secret_patch(secret, record), sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
