#!/usr/bin/env python3
"""Return a Knowledge Vault bearer header for Codex's HTTP header helper."""

import argparse
import json
from pathlib import Path

from knowledge_vault.auth.device_tokens import read_token


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-file", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({"Authorization": f"Bearer {read_token(args.token_file)}"}))


if __name__ == "__main__":
    main()
