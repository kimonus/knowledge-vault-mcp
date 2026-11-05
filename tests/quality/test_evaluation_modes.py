"""Personal acceptance never invents independent reviews or silently pools evidence arms."""

import copy
import json
import subprocess
import sys
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from .client_packets import build_client_packets
from .corpus import cases
from .evaluation import TruthAudit, truth_card_digest, validate_truth_audits
from .judgments import SoloCaseJudgment, score_judgments

CASE = next(case for case in cases() if case.id == "decision-0")
TRACE = "a" * 64


def provenance(kind="model", digest=TRACE):
    return {
        "kind": kind,
        "method": "recorded-test-method",
        "recorded_at": "2026-11-05T12:00:00Z",
        "evidence_sha256": digest,
    }


def solo(labels=None, kind="model"):
    return {
        "case_id": CASE.id,
        "run_id": "r01",
        "review": {"reviewer_id": "operator", "provenance": provenance(kind), **(labels or {})},
    }


def audit(reviewer="operator", kind="human", independent=False, artifact="b" * 64):
    source = sha256(CASE.source.encode()).hexdigest()
    return {
        "case_id": CASE.id,
        "audit_id": reviewer + "-audit",
        "reviewer_id": reviewer,
        "source_sha256": source,
        "truth_card_sha256": truth_card_digest(CASE),
        "artifact_sha256": artifact,
        "provenance": provenance(kind, source),
        "verdict": "accepted",
        "independent": independent,
        "before_client_output": independent,
    }


def receipt(mode="lexical_only"):
    return {
        "case_id": CASE.id,
        "run_id": "r01",
        "kind": "assistant_client",
        "client_name": "external-assistant",
        "client_version": "test",
        "client_model": "recorded-model",
        "server_revision": "c" * 40,
        "schema_revision": "0006_temporal_evidence",
        "retrieval_mode": mode,
        "embedding_model": None if mode == "lexical_only" else "offline-model",
        "corpus_sha256": build_client_packets((CASE,))["corpus_sha256"],
        "source_sha256": sha256(CASE.source.encode()).hexdigest(),
        "trace_sha256": TRACE,
        "trace_ref": "operator-local/run/trace.json",
    }


def complete_labels():
    return {
        "source_outcome": "reviewed",
        "matched_gold_keys": [c.key for c in CASE.claims],
        "unsupported_claims": 0,
        "extracted_claims_count": len(CASE.claims),
        "retrieved_gold_keys": list(CASE.relevant),
        "answer_correct": True,
        "grounded": True,
        "citations_correct": True,
        "abstained": False,
    }


def score(raw, **kwargs):
    return score_judgments((CASE,), raw, evaluation_mode="solo_operator", **kwargs)


def test_solo_one_recorded_evaluator_and_missing_labels():
    result = score(
        [solo({"answer_correct": True})],
        packet_manifest=build_client_packets((CASE,), repetitions=3),
    )
    assert result["evaluation_mode"] == "solo_operator"
    assert result["retrieval_mode"] == "lexical_only"
    assert result["run_completeness"]["missing_runs"] == 2
    assert result["metrics"]["grounded"]["missing_labels"] == 1
    assert result["metrics_by_judgment_kind"]["model"]["answer_correct"]["value"] == 1
    assert result["full_agreement"] is None
    assert not result["acceptance_evidence_complete"]
    assert not result["provenance"]["independence_verified_by_scorer"]


def test_solo_complete_labels_require_audit_and_client_evidence_for_acceptance_readiness():
    manifest = build_client_packets((CASE,))
    result = score([solo(complete_labels())], packet_manifest=manifest)
    assert result["study_complete"]
    assert not result["acceptance_evidence_complete"]
    result = score(
        [solo(complete_labels())],
        packet_manifest=manifest,
        truth_audits=[audit()],
        client_runs=[receipt()],
    )
    assert result["acceptance_evidence_complete"]
    assert result["quality_gate_status"] == "not_decided_by_scorer"
    assert result["provenance"]["effective_reviews_by_kind"] == {"model": 1}


def test_complete_failed_results_are_not_missing_data_or_an_automatic_human_gate():
    labels = complete_labels()
    labels.update(answer_correct=False, grounded=False, citations_correct=False)
    raw = solo(labels)
    raw["checks"] = [
        {"name": "no_rejected_items", "passed": False, "provenance": provenance("deterministic")}
    ]
    result = score(
        [raw],
        packet_manifest=build_client_packets((CASE,)),
        truth_audits=[audit(kind="model")],
        client_runs=[receipt()],
    )
    assert result["acceptance_evidence_complete"]
    assert result["automated_checks"]["failed"] == 1
    assert result["provenance"]["pending_manual_reviews"] == 0
    assert result["answer_correct"] == 0
    assert result["quality_gate_status"] == "not_decided_by_scorer"


def test_failed_checks_request_manual_review_and_keep_machine_judgment():
    raw = solo({"answer_correct": False})
    raw["needs_manual_review"] = True
    raw["checks"] = [
        {"name": "receipt-consistency", "passed": False, "provenance": provenance("deterministic")}
    ]
    result = score([raw])
    assert result["provenance"]["pending_manual_reviews"] == 1
    raw["manual_review"] = {
        "reviewer_id": "operator",
        "provenance": provenance("human"),
        "answer_correct": False,
    }
    result = score([raw])
    assert result["provenance"]["pending_manual_reviews"] == 0
    assert result["provenance"]["submitted_reviews_by_kind"] == {"human": 1, "model": 1}
    assert result["metrics_by_judgment_kind"]["human"]["answer_correct"]["value"] == 0
    assert result["automated_checks"] == {"passed": 0, "failed": 1}


def test_explicit_ambiguity_and_machine_manual_override():
    raw = solo()
    raw["needs_manual_review"] = True
    assert score([raw])["provenance"]["pending_manual_reviews"] == 1
    raw["manual_review"] = raw["review"]
    with pytest.raises(ValueError, match="human evaluator"):
        score([raw])


@pytest.mark.parametrize(
    "field", ["grounded", "answer_correct", "citations_correct", "matched_gold_keys", "abstained"]
)
def test_deterministic_checks_cannot_masquerade_as_semantic_judgments(field):
    with pytest.raises(ValueError, match="semantic review"):
        score([solo({field: [] if field == "matched_gold_keys" else True}, kind="deterministic")])


def test_missing_or_misleading_provenance_is_rejected():
    raw = solo()
    del raw["review"]["provenance"]
    with pytest.raises(ValueError):
        score([raw])
    raw = solo()
    raw["review"]["provenance"]["recorded_at"] = "2026-11-05T12:00:00"
    with pytest.raises(ValueError):
        score([raw])
    raw = solo()
    raw["checks"] = [{"name": "check", "passed": True, "provenance": provenance("model")}]
    with pytest.raises(ValueError, match="deterministic provenance"):
        score([raw])


def test_independent_mode_keeps_two_reviewers_and_rejects_solo_or_machine_substitution():
    with pytest.raises(ValueError):
        score_judgments((CASE,), [solo()], evaluation_mode="independent_review")
    raw = {
        "case_id": CASE.id,
        "reviewers": [{"reviewer_id": "a"}, {"reviewer_id": " A "}],
        "adjudicated": {"reviewer_id": "a"},
    }
    with pytest.raises(ValueError, match="distinct reviewers"):
        score_judgments((CASE,), [raw])
    raw["reviewers"][1] = {"reviewer_id": "b", "provenance": provenance("model")}
    with pytest.raises(ValueError, match="substitute machine"):
        score_judgments((CASE,), [raw])
    raw["reviewers"] = [{"reviewer_id": "a"}, {"reviewer_id": "b"}]
    result = score_judgments((CASE,), [raw])
    assert result["evaluation_mode"] == "independent_review"
    assert result["provenance"]["effective_reviews_by_kind"] == {"unrecorded": 1}
    assert not result["acceptance_evidence_complete"]


@pytest.mark.parametrize("change", ["identical", "reviewer_id", "audit_id"])
def test_identical_or_copied_truth_audits_never_become_two_reviews(change):
    first = audit("a", independent=True)
    second = copy.deepcopy(first)
    if change != "identical":
        second[change] = "different"
    with pytest.raises(ValueError, match="duplicate truth"):
        validate_truth_audits((CASE,), [first, second], "independent_review")
    second.update(reviewer_id="b", audit_id="different-b")
    with pytest.raises(ValueError, match="copied audit"):
        validate_truth_audits((CASE,), [first, second], "independent_review")


def test_two_agreeing_humans_with_distinct_sealed_artifacts_are_valid():
    result = validate_truth_audits(
        (CASE,),
        [audit("a", independent=True), audit("b", independent=True, artifact="d" * 64)],
        "independent_review",
    )
    assert result["complete"]
    assert result["independence"] == "operator_attested"
    assert not validate_truth_audits((CASE,), [audit("a", independent=True)], "independent_review")[
        "complete"
    ]
    assert validate_truth_audits((CASE,), [audit(kind="model")], "solo_operator")["complete"]
    with pytest.raises(ValueError, match="human audits"):
        validate_truth_audits((CASE,), [audit()], "independent_review")
    with pytest.raises(ValueError, match="machine truth audits"):
        validate_truth_audits(
            (CASE,), [audit(kind="model", independent=True)], "independent_review"
        )
    with pytest.raises(ValueError, match="semantic truth card"):
        validate_truth_audits((CASE,), [audit(kind="deterministic")], "solo_operator")
    retrospective = audit("a", independent=True)
    retrospective["before_client_output"] = False
    with pytest.raises(ValueError, match="human audits"):
        validate_truth_audits((CASE,), [retrospective], "independent_review")


def test_truth_revision_source_binding_and_unknown_cases():
    raw = audit()
    raw.update(verdict="needs_revision", notes="source lacks the gold observation date")
    assert not validate_truth_audits((CASE,), [raw], "solo_operator")["complete"]
    raw["notes"] = ""
    with pytest.raises(ValueError, match="explanation"):
        validate_truth_audits((CASE,), [raw], "solo_operator")
    raw = audit()
    raw["source_sha256"] = "e" * 64
    raw["provenance"]["evidence_sha256"] = "e" * 64
    with pytest.raises(ValueError, match="source digest"):
        validate_truth_audits((CASE,), [raw], "solo_operator")
    changed_labels = replace(CASE, claims=())
    with pytest.raises(ValueError, match="card digest"):
        validate_truth_audits((changed_labels,), [audit()], "solo_operator")


def test_arms_and_client_trace_provenance_cannot_be_pooled_or_forged():
    with pytest.raises(ValueError, match="cannot pool"):
        score([solo()], client_runs=[receipt("hybrid")])
    raw = receipt()
    raw["embedding_model"] = "unexpected-model"
    with pytest.raises(ValueError, match="null embedding"):
        score([], client_runs=[raw])
    raw = receipt()
    raw["trace_sha256"] = "d" * 64
    with pytest.raises(ValueError, match="client trace"):
        score([solo()], client_runs=[raw])
    with pytest.raises(ValueError, match="duplicate client"):
        score([], client_runs=[receipt(), receipt()])
    repeated_trace = receipt()
    repeated_trace["run_id"] = "r02"
    with pytest.raises(ValueError, match="trace cannot be reused"):
        score([], client_runs=[receipt(), repeated_trace])
    raw = receipt()
    raw["kind"] = "sdk_smoke"
    with pytest.raises(ValueError):
        score([], client_runs=[raw])


def test_schemas_and_cli_modes_and_private_error_redaction(tmp_path: Path):
    assert "$defs" in TypeAdapter(list[SoloCaseJudgment]).json_schema()
    assert "$defs" in TypeAdapter(list[TruthAudit]).json_schema()
    path = tmp_path / "judgments.json"
    path.write_text("[]")
    for mode in ["solo_operator", "independent_review"]:
        completed = subprocess.run(  # noqa: S603 - fixed CLI and temporary test inputs
            [
                sys.executable,
                "scripts/quality_score_judgments.py",
                str(path),
                "--evaluation-mode",
                mode,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        assert result["evaluation_mode"] == mode
        assert not result["acceptance_evidence_complete"]
    path.write_text(json.dumps([solo({"grounded": "private-source-marker"})]))
    completed = subprocess.run(  # noqa: S603 - fixed CLI and temporary test inputs
        [sys.executable, "scripts/quality_score_judgments.py", str(path)],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "private-source-marker" not in completed.stderr


def test_readable_truth_cards_are_unjudged_and_cannot_be_overwritten(tmp_path: Path):
    path = tmp_path / "audit"
    command = [sys.executable, "scripts/quality_truth_audit.py", "--output", str(path)]
    subprocess.run(command, check=True, capture_output=True)  # noqa: S603
    assert "solo_operator" in (path / "cards.md").read_text()
    raw = json.loads((path / "audit-1.template.json").read_text())
    assert len(raw) == 41
    assert not (path / "audit-2.template.json").exists()
    with pytest.raises(ValueError):
        validate_truth_audits(cases(), raw, "solo_operator")
    assert subprocess.run(command, capture_output=True).returncode == 2  # noqa: S603


def test_independent_complete_recorded_evidence_preserves_strict_research_path():
    labels = complete_labels()
    record = {
        "case_id": CASE.id,
        "run_id": "r01",
        "reviewers": [
            {"reviewer_id": who, "provenance": provenance("human"), **labels} for who in ["a", "b"]
        ],
        "adjudicated": {"reviewer_id": "a", "provenance": provenance("human"), **labels},
    }
    result = score_judgments(
        (CASE,),
        [record],
        evaluation_mode="independent_review",
        packet_manifest=build_client_packets((CASE,)),
        client_runs=[receipt()],
        truth_audits=[
            audit("a", independent=True),
            audit("b", independent=True, artifact="d" * 64),
        ],
    )
    assert result["acceptance_evidence_complete"]
    assert result["full_agreement"] == 1
    assert result["quality_gate_status"] == "not_decided_by_scorer"
    assert not result["provenance"]["independence_verified_by_scorer"]


def test_copied_audit_files_cannot_count_as_two_independent_reviews(tmp_path: Path):
    original = tmp_path / "original.json"
    copied = tmp_path / "copy.json"
    content = json.dumps([audit("a", independent=True)])
    original.write_text(content)
    copied.write_text(content)
    combined = json.loads(original.read_text()) + json.loads(copied.read_text())
    with pytest.raises(ValueError, match="duplicate truth"):
        validate_truth_audits((CASE,), combined, "independent_review")


def test_solo_duplicates_and_invalid_labels_do_not_bypass_existing_validation():
    with pytest.raises(ValueError, match="repeated case/run"):
        score([solo(), solo()])
    with pytest.raises(ValueError, match="retrieved key"):
        score([solo({"retrieved_gold_keys": ["invented"]})])
    with pytest.raises(ValueError, match="correct and abstained"):
        score([solo({"answer_correct": True, "abstained": True})])


def test_existing_dev_corpus_and_packet_totals_are_preserved():
    manifest = build_client_packets(cases(), split="dev", repetitions=3)
    assert manifest["case_count"] == 41
    assert manifest["run_count"] == 123
    assert (
        manifest["corpus_sha256"]
        == "1afc3456c9f47938e45b33c1d5497c5861d961c219dd91a5a78081bb5d8a279a"
    )
