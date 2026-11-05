#!/usr/bin/env python3
"""Export readable source/truth cards and blank, mode-specific operator audit templates."""

import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from quality.corpus import cases
from quality.evaluation import truth_card_digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evaluation-mode",
        choices=("solo_operator", "independent_review"),
        default="solo_operator",
    )
    parser.add_argument("--split", choices=("dev", "held_out"), default="dev")
    parser.add_argument(
        "--output", type=Path, required=True, help="new private directory outside Git"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.output.resolve().is_relative_to(root) or args.output.exists():
        parser.error("output must be a new operator-local directory outside the repository")
    args.output.mkdir(mode=0o700, parents=True)
    selected = tuple(case for case in cases() if case.split == args.split)
    cards = [
        "# Source-truth audit",
        "",
        f"Evaluation mode: `{args.evaluation_mode}`.",
        "",
        "Read the original source first. Is every expected claim/date/identity supported? "
        "Record accepted, needs revision, or leave unjudged. Never infer missing dates or aliases.",
        "",
    ]
    template = []
    for case in selected:
        cards.extend(
            [f"## {case.id}", "", "### Source", "", case.source, "", "### Expected claims", ""]
        )
        for claim in case.claims:
            cards.extend([f"- {claim.payload['content']}"])
            for field in ("observed_at", "valid_from", "valid_to", "temporal"):
                if field in claim.payload:
                    cards.append(
                        f"  - {field}: `{json.dumps(claim.payload[field], ensure_ascii=False)}`"
                    )
        if not case.claims:
            cards.append("No required gold claims.")
        if case.state:
            expected_state = {
                "request": case.state.request,
                "outcome": case.state.outcome,
                "values": case.state.values,
            }
            cards.extend(
                [
                    "",
                    "Expected state (review only; never show to client):",
                    f"`{json.dumps(expected_state)}`",
                ]
            )
        cards.extend(["", "Decision: ______  Explanation: ______", ""])
        digest = sha256(case.source.encode()).hexdigest()
        template.append(
            {
                "case_id": case.id,
                "audit_id": None,
                "reviewer_id": None,
                "source_sha256": digest,
                "truth_card_sha256": truth_card_digest(case),
                "artifact_sha256": None,
                "provenance": {
                    "kind": None,
                    "method": None,
                    "recorded_at": None,
                    "evidence_sha256": digest,
                },
                "verdict": None,
                "independent": False,
                "before_client_output": False,
                "notes": "",
            }
        )
    paths = {"cards.md": "\n".join(cards)}
    for number in range(2 if args.evaluation_mode == "independent_review" else 1):
        paths[f"audit-{number + 1}.template.json"] = json.dumps(template, indent=2) + "\n"
    for name, content in paths.items():
        path = args.output / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)
    print(
        f"Prepared {len(selected)} {args.evaluation_mode} cards; "
        "templates are unjudged, not reviews."
    )


if __name__ == "__main__":
    main()
