#!/usr/bin/env python3
"""Run the synthetic quality baseline on Testcontainers PostgreSQL.

This script never accepts a database URL. tests/conftest.py starts a disposable PostgreSQL
container, applies migrations, and clears it between test runs. No production data is read.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("text", "fake", "local"), default="text")
    parser.add_argument("--full", action="store_true", help="run all 63 synthetic source packs")
    parser.add_argument(
        "--split", choices=("dev", "held_out"), help="evaluate only one frozen split"
    )
    parser.add_argument("--distractors", type=int, default=0)
    parser.add_argument("--report", type=Path, help="write a redacted JSON report here")
    parser.add_argument("--revision", help="Git commit SHA of the code under test")
    args = parser.parse_args()
    if not 0 <= args.distractors <= 1000:
        parser.error("--distractors must be between 0 and 1000")
    with tempfile.TemporaryDirectory(prefix="kv-quality-") as temporary:
        report_path = args.report or Path(temporary) / "report.json"
        environment = os.environ.copy()
        environment.update(
            {
                "KNOWLEDGE_VAULT_QUALITY_MODE": args.mode,
                "KNOWLEDGE_VAULT_QUALITY_FULL": "1" if args.full else "0",
                "KNOWLEDGE_VAULT_QUALITY_SPLIT": args.split or "all",
                "KNOWLEDGE_VAULT_QUALITY_DISTRACTORS": str(args.distractors),
                "KNOWLEDGE_VAULT_QUALITY_REPORT": str(report_path.resolve()),
                "KNOWLEDGE_VAULT_QUALITY_REVISION": args.revision or "unavailable",
            }
        )
        command = [sys.executable, "-m", "pytest", "-q", "tests/quality/test_benchmark.py"]
        result = subprocess.run(command, env=environment, check=False)  # noqa: S603
        if result.returncode:
            return result.returncode
        report = json.loads(report_path.read_text(encoding="utf-8"))
        print(json.dumps({"metadata": report["metadata"], "summary": report["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
