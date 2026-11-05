#!/usr/bin/env python3
"""Score auditable solo-operator or independent-review client judgments."""

import argparse
import json
import sys
from pathlib import Path

from pydantic import TypeAdapter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from quality.corpus import cases
from quality.evaluation import ClientRun, TruthAudit
from quality.judgments import CaseJudgment, SoloCaseJudgment, score_judgments


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("judgments", type=Path, nargs="?", help="operator-local JSON list")
    parser.add_argument(
        "--evaluation-mode",
        choices=("solo_operator", "independent_review"),
        default="solo_operator",
        help="solo is personal engineering acceptance; independent is optional research",
    )
    parser.add_argument(
        "--retrieval-mode", choices=("lexical_only", "hybrid"), default="lexical_only"
    )
    parser.add_argument("--truth-audits", type=Path, help="operator-local source-truth audit list")
    parser.add_argument(
        "--client-runs", type=Path, help="actual assistant-client execution receipts"
    )
    parser.add_argument("--markdown", type=Path, help="write a redacted human-readable summary")
    parser.add_argument(
        "--schema",
        choices=("judgments", "truth-audits", "client-runs"),
        help="print JSON Schema for the selected mode without scoring",
    )
    parser.add_argument(
        "--packets",
        type=Path,
        help="generated blinded packet manifest for expected-run validation",
    )
    parser.add_argument("--report", type=Path, help="write aggregate JSON here")
    args = parser.parse_args()
    if args.schema:
        schemas = {
            "judgments": TypeAdapter(
                list[SoloCaseJudgment]
                if args.evaluation_mode == "solo_operator"
                else list[CaseJudgment]
            ),
            "truth-audits": TypeAdapter(list[TruthAudit]),
            "client-runs": TypeAdapter(list[ClientRun]),
        }
        print(json.dumps(schemas[args.schema].json_schema(), indent=2))
        return
    if args.judgments is None:
        parser.error("judgments are required unless --schema is selected")
    raw = json.loads(args.judgments.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        parser.error("judgments must be a JSON list")
    packet_manifest = json.loads(args.packets.read_text(encoding="utf-8")) if args.packets else None
    if packet_manifest is not None and not isinstance(packet_manifest, dict):
        parser.error("packet manifest must be a JSON object")

    def read_list(path: Path | None) -> list:
        value = json.loads(path.read_text(encoding="utf-8")) if path else []
        if not isinstance(value, list):
            parser.error("audit and client evidence files must be JSON lists")
        return value

    try:
        scored = score_judgments(
            cases(),
            raw,
            packet_manifest=packet_manifest,
            evaluation_mode=args.evaluation_mode,
            retrieval_mode=args.retrieval_mode,
            truth_audits=read_list(args.truth_audits),
            client_runs=read_list(args.client_runs),
        )
    except ValueError:
        # Pydantic errors may echo private input. Do not print raw validation exceptions.
        parser.error(
            "invalid judgments, provenance, truth audits, or client evidence; "
            "check the selected schemas"
        )
    serialized = json.dumps(scored, indent=2) + "\n"
    if args.report:
        args.report.write_text(serialized, encoding="utf-8")
    if args.markdown:
        args.markdown.write_text(
            "# Client evaluation report\n\n"
            f"Evaluation mode: `{scored['evaluation_mode']}`. "
            f"Retrieval mode: `{scored['retrieval_mode']}`.\n\n"
            f"Judged runs: {scored['judged_runs']}. "
            f"Missing judgments: {scored['run_completeness']['missing_runs']}.\n\n"
            f"Client receipt runs: {scored['client_evidence']['observed_runs']}. "
            f"Pending manual reviews: {scored['provenance']['pending_manual_reviews']}.\n\n"
            f"Acceptance evidence complete: {scored['acceptance_evidence_complete']}. "
            "Quality gate: not decided by the scorer.\n\n"
            "Machine and human labels are separated in the JSON report. "
            "A schema-valid receipt does not prove a real assistant or reviewer independence.\n",
            encoding="utf-8",
        )
    print(serialized, end="")


if __name__ == "__main__":
    main()
