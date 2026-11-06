"""Pilot preparation never becomes client execution or a complete study."""

import json
import subprocess
import sys

from .client_packets import build_client_packets
from .corpus import cases
from .judgments import score_judgments


def test_prepared_pilot_is_blinded_bounded_and_missing_evidence_stays_missing(tmp_path):
    output = tmp_path / "pilot"
    command = [sys.executable, "scripts/quality_prepare_client_pilot.py", "--output", str(output)]
    result = subprocess.run(  # noqa: S603 - fixed CLI and temporary output
        command, capture_output=True, text=True, check=True
    )
    assert "executed 0" in result.stdout
    plan = json.loads((output / "pilot-plan.json").read_text())
    packets = json.loads((output / "pilot-packets.json").read_text())
    full = json.loads((output / "development-packets.json").read_text())
    assert plan["planned_pilot_runs"] == packets["run_count"] == 6
    assert plan["planned_development_runs"] == full["run_count"] == 123
    assert full == build_client_packets(cases(), repetitions=3)
    selected = tuple(case for case in cases() if case.id in plan["pilot_case_ids"])
    assert packets == build_client_packets(selected)
    assert plan["status"] == "NOT RUN" and plan["client_identity_and_settings"] is None
    assert plan["evaluation_mode"] == "solo_operator"
    assert plan["retrieval_mode"] == "lexical_only" and plan["embedding_model"] is None
    assert sorted(plan["packet_indices_in_execution_order"]) == list(range(6))
    assert (output.stat().st_mode & 0o777) == 0o700
    for path in output.iterdir():
        assert (path.stat().st_mode & 0o777) == 0o600
    report = score_judgments(selected, [], packet_manifest=packets, evaluation_mode="solo_operator")
    assert report["run_completeness"]["missing_runs"] == 6
    assert not report["acceptance_evidence_complete"] and not report["study_complete"]
    assert subprocess.run(command, capture_output=True).returncode != 0  # noqa: S603


def test_preparation_rejects_repository_output_and_is_reproducible(tmp_path):
    base = [sys.executable, "scripts/quality_prepare_client_pilot.py", "--output"]
    denied = subprocess.run(  # noqa: S603 - fixed negative CLI input
        [*base, "tests/quality/forbidden-pilot"], capture_output=True
    )
    assert denied.returncode != 0
    for name in ("a", "b"):
        subprocess.run(  # noqa: S603 - fixed CLI and temporary output
            [*base, str(tmp_path / name)], capture_output=True, check=True
        )
    for name in ("pilot-plan.json", "pilot-packets.json", "development-packets.json"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
