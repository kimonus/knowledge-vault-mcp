#!/usr/bin/env python3
"""Enforce separate statement and branch thresholds from coverage.py JSON."""

import json
import sys
from pathlib import Path


def main() -> None:
    report_path = Path(sys.argv[1] if len(sys.argv) > 1 else "coverage.json")
    totals = json.loads(report_path.read_text(encoding="utf-8"))["totals"]
    statements = 100 * totals["covered_lines"] / max(1, totals["num_statements"])
    branches = 100 * totals["covered_branches"] / max(1, totals["num_branches"])
    print(f"statement coverage: {statements:.2f}% (required 90.00%)")
    print(f"branch coverage: {branches:.2f}% (required 85.00%)")
    if statements < 90 or branches < 85:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
