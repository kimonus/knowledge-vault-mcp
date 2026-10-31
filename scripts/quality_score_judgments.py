#!/usr/bin/env python3
"""Score two-reviewer judgments of client extraction and answers, without raw text output."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from quality.corpus import cases
from quality.judgments import score_judgments


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("judgments", type=Path, help="operator-local JSON file; do not commit it")
    parser.add_argument("--report", type=Path, help="write aggregate JSON here")
    args = parser.parse_args()
    raw = json.loads(args.judgments.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        parser.error("judgments must be a JSON list")
    scored = score_judgments(cases(), raw)
    serialized = json.dumps(scored, indent=2) + "\n"
    if args.report:
        args.report.write_text(serialized, encoding="utf-8")
    print(serialized, end="")


if __name__ == "__main__":
    main()
