#!/usr/bin/env python3
"""Prepare a bounded, blinded lexical-only pilot; never execute or simulate a client."""

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from quality.client_packets import build_client_packets
from quality.corpus import cases

PILOT_IDS = (
    "current-0",
    "decision-0",
    "correction-0",
    "multilingual-0",
    "conflict-3",
    "no-record-0",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="new private directory outside Git"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.is_relative_to(root) or output.exists():
        parser.error("output must be a new directory outside the repository")
    corpus = cases()
    selected = tuple(case for case in corpus if case.id in PILOT_IDS)
    pilot = build_client_packets(selected, split="dev", repetitions=1)
    full = build_client_packets(corpus, split="dev", repetitions=3)
    order = list(range(pilot["run_count"]))
    random.Random(0).shuffle(order)  # noqa: S311 - experiment ordering, not security
    revision = subprocess.check_output(
        ["/usr/bin/git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    clean = not subprocess.check_output(
        ["/usr/bin/git", "status", "--porcelain"], cwd=root, text=True
    )
    plan = {
        "schema_version": 1,
        "evaluation_mode": "solo_operator",
        "retrieval_mode": "lexical_only",
        "embedding_model": None,
        "status": "NOT RUN",
        "server_revision": revision,
        "server_worktree_clean": clean,
        "parent_dev_corpus_sha256": full["corpus_sha256"],
        "pilot_corpus_sha256": pilot["corpus_sha256"],
        "pilot_case_ids": [case.id for case in selected],
        "planned_pilot_runs": pilot["run_count"],
        "planned_development_runs": full["run_count"],
        "ordering_seed": 0,
        "packet_indices_in_execution_order": order,
        "timing": "monotonic wall time per actual client run; capture UTC boundaries separately",
        "client_identity_and_settings": None,
        "execution_prerequisites": [
            "Freeze client version, model and settings with separate evaluation authentication",
            "Require clean reviewed server revision, matching fresh lab manifest and source audits",
            "Fresh lab and client session per packet; no expected cards in client context",
        ],
        "expansion_rule": (
            "Do not execute 123 runs until actual pilot evidence is independently checked"
        ),
        "m1_acceptance": "not_decided; use unchanged benchmark criteria and operator decision",
    }
    output.mkdir(mode=0o700, parents=True)
    for name, data in (
        ("pilot-plan.json", plan),
        ("pilot-packets.json", pilot),
        ("development-packets.json", full),
        ("client-runs.json", []),
        ("judgments.json", []),
    ):
        with (output / name).open("x", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        (output / name).chmod(0o600)
    print(
        f"Prepared {pilot['run_count']} pilot and {full['run_count']} development packets; "
        "executed 0"
    )


if __name__ == "__main__":
    main()
