#!/usr/bin/env python3
"""Write blinded synthetic cases for operator-local MCP client evaluation."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from quality.client_packets import build_client_packets
from quality.corpus import cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "held_out"), default="dev")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True, help="operator-local JSON output")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; do not overwrite a prior client run packet set")
    try:
        packet_set = build_client_packets(cases(), split=args.split, repetitions=args.repetitions)
    except ValueError as error:
        parser.error(str(error))
    args.output.write_text(json.dumps(packet_set, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {packet_set['run_count']} blinded synthetic packets to {args.output}")


if __name__ == "__main__":
    main()
