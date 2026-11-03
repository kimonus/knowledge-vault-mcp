"""Independent human-label scoring rejects self-inconsistent cards."""

from typing import Any

import pytest

from .corpus import cases
from .judgments import score_judgments


def test_judgment_aggregates_do_not_include_source_or_answer_text():
    case = next(case for case in cases() if case.id == "decision-0")
    key = case.claims[0].key
    review = {
        "matched_gold_keys": [key],
        "unsupported_claims": 0,
        "answer_correct": True,
        "grounded": True,
        "citations_correct": True,
        "abstained": False,
    }
    result = score_judgments(
        (case,),
        [
            {
                "case_id": case.id,
                "reviewers": [
                    {"reviewer_id": "reader-one", **review},
                    {"reviewer_id": "reader-two", **review},
                ],
                "adjudicated": {"reviewer_id": "adjudicator", **review},
            }
        ],
    )
    assert result["extraction_claim_coverage"] == 0.5
    assert result["answer_correct"] == 1.0
    assert result["full_agreement"] == 1.0
    assert "source" not in result and "answer" not in result


def test_judgments_reject_unknown_key_and_duplicate_case():
    case = cases()[0]
    bad = {
        "case_id": case.id,
        "reviewers": [{"reviewer_id": "a"}, {"reviewer_id": "b"}],
        "adjudicated": {
            "reviewer_id": "c",
            "matched_gold_keys": ["invented"],
            "unsupported_claims": 0,
        },
    }
    with pytest.raises(ValueError, match="matched key"):
        score_judgments((case,), [bad])
    bad["adjudicated"] = {"reviewer_id": "c"}
    with pytest.raises(ValueError, match="repeated case"):
        score_judgments((case,), [bad, bad])


def test_no_record_judgment_does_not_divide_by_zero():
    case = next(case for case in cases() if case.category == "no_record")
    review = {
        "matched_gold_keys": [],
        "unsupported_claims": 1,
        "abstained": True,
    }
    result = score_judgments(
        (case,),
        [
            {
                "case_id": case.id,
                "reviewers": [
                    {"reviewer_id": "reader-one", **review},
                    {"reviewer_id": "reader-two", **review},
                ],
                "adjudicated": {"reviewer_id": "adjudicator", **review},
            }
        ],
    )
    assert result["coverage_cases"] == 0
    assert result["unsupported_claims_total"] == 1
    assert result["abstention_correct"] == 1.0


def test_source_processing_is_scored_separately_from_claim_extraction():
    selected = [
        next(case for case in cases() if case.id == case_id)
        for case_id in ("decision-0", "preference-0", "no-record-0")
    ]
    outcomes = ("reviewed", "partial", "unavailable")
    judgments = [
        {
            "case_id": case.id,
            "reviewers": [
                {"reviewer_id": "one", "source_outcome": outcome},
                {"reviewer_id": "two", "source_outcome": outcome},
            ],
            "adjudicated": {"reviewer_id": "adjudicator", "source_outcome": outcome},
        }
        for case, outcome in zip(selected, outcomes, strict=True)
    ]
    result = score_judgments(tuple(selected), judgments)
    assert result["source_outcomes"] == {
        "reviewed": 1,
        "partial": 1,
        "unprocessed": 0,
        "unavailable": 1,
    }
    assert result["source_processing_cases"] == 2
    assert result["source_processing_coverage"] == 0.5
    assert result["source_agreement"] == 1.0
    assert result["extraction_claim_coverage"] is None
    assert result["by_split"]["dev"]["source_processing_coverage"] == 0.5


def test_invalid_source_outcome_is_rejected():
    case = cases()[0]
    raw = {
        "case_id": case.id,
        "reviewers": [{"reviewer_id": "one"}, {"reviewer_id": "two"}],
        "adjudicated": {"reviewer_id": "adjudicator", "source_outcome": "invented"},
    }
    with pytest.raises(ValueError, match="source_outcome"):
        score_judgments((case,), [raw])


def test_repeated_client_runs_are_scored_separately():
    case = next(case for case in cases() if case.id == "decision-0")
    records = [
        {
            "case_id": case.id,
            "run_id": run_id,
            "reviewers": [
                {"reviewer_id": "one", "answer_correct": answer_correct},
                {"reviewer_id": "two", "answer_correct": answer_correct},
            ],
            "adjudicated": {"reviewer_id": "adjudicator", "answer_correct": answer_correct},
        }
        for run_id, answer_correct in (("r01", True), ("r02", False))
    ]
    result = score_judgments((case,), records)
    assert result["judged_cases"] == 1
    assert result["judged_runs"] == 2
    assert result["runs_per_case_min"] == result["runs_per_case_max"] == 2
    assert result["balanced_runs"] is True
    assert result["answer_correct"] == 0.5
    with pytest.raises(ValueError, match="repeated case/run ID"):
        score_judgments((case,), [records[0], records[0]])


def test_invalid_run_id_is_rejected():
    case = cases()[0]
    record = {
        "case_id": case.id,
        "run_id": "bad/escape",
        "reviewers": [{"reviewer_id": "one"}, {"reviewer_id": "two"}],
        "adjudicated": {"reviewer_id": "adjudicator"},
    }
    with pytest.raises(ValueError, match="run_id"):
        score_judgments((case,), [record])


def test_pilot_metrics_separate_abstention_and_missing_labels_by_split():
    selected = tuple(
        next(case for case in cases() if case.id == case_id)
        for case_id in ("decision-0", "no-record-0", "decision-4", "no-record-1")
    )

    def card(case_id: str, labels: dict[str, Any]) -> dict[str, Any]:
        return {
            "case_id": case_id,
            "run_id": "r01",
            "reviewers": [
                {"reviewer_id": "first", **labels},
                {"reviewer_id": "second", **labels},
            ],
            "adjudicated": {"reviewer_id": "adjudicator", **labels},
        }

    raw = [
        card(
            "decision-0",
            {
                "source_outcome": "reviewed",
                "matched_gold_keys": [selected[0].claims[0].key],
                "unsupported_claims": 1,
                "extracted_claims_count": 2,
                "retrieved_gold_keys": [selected[0].relevant[0]],
                "answer_correct": True,
                "grounded": True,
                "citations_correct": True,
                "abstained": False,
            },
        ),
        card(
            "no-record-0",
            {
                "source_outcome": "unavailable",
                "matched_gold_keys": [],
                "unsupported_claims": 0,
                "extracted_claims_count": 0,
                "answer_correct": True,
                "grounded": True,
                "citations_correct": True,
                "abstained": True,
            },
        ),
        card(
            "decision-4",
            {
                "source_outcome": "partial",
                "matched_gold_keys": [],
                "unsupported_claims": 0,
                "extracted_claims_count": 0,
                "retrieved_gold_keys": [],
                "answer_correct": False,
                "grounded": False,
                "citations_correct": False,
                "abstained": True,
            },
        ),
        card("no-record-1", {}),
    ]
    result = score_judgments(selected, raw)
    assert result["schema_version"] == 2
    assert result["metrics"]["correct_abstention"] == {
        "numerator": 1,
        "denominator": 1,
        "eligible_runs": 2,
        "labeled_runs": 1,
        "missing_labels": 1,
        "value": 1.0,
    }
    assert result["metrics"]["incorrect_abstention"]["numerator"] == 1
    assert result["metrics"]["incorrect_abstention"]["denominator"] == 2
    assert result["metrics"]["answerable_answer_correct"]["numerator"] == 1
    assert result["metrics"]["answerable_answer_correct"]["denominator"] == 2
    assert result["metrics"]["answer_correct"]["missing_labels"] == 1
    assert result["metrics"]["grounded"]["numerator"] == 2
    assert result["metrics"]["citations_correct"]["denominator"] == 3
    assert result["metrics"]["source_processing_coverage"]["numerator"] == 1
    assert result["metrics"]["source_processing_coverage"]["denominator"] == 2
    assert result["metrics"]["source_processing_coverage"]["missing_labels"] == 1
    assert result["metrics"]["extraction_claim_coverage"]["numerator"] == 1
    assert result["metrics"]["extraction_claim_coverage"]["denominator"] == 4
    assert result["metrics"]["unsupported_extracted_claim_rate"]["numerator"] == 1
    assert result["metrics"]["unsupported_extracted_claim_rate"]["denominator"] == 2
    assert result["unsupported_claims_total"] == 1
    assert result["unsupported_claims_missing_labels"] == 1
    assert result["metrics"]["retrieval_recall"]["numerator"] == 1
    assert result["metrics"]["retrieval_recall"]["denominator"] == 2
    assert result["metrics_by_split"]["dev"]["correct_abstention"]["value"] == 1.0
    assert result["metrics_by_split"]["held_out"]["correct_abstention"]["missing_labels"] == 1
    assert result["metrics_by_split"]["held_out"]["incorrect_abstention"]["value"] == 1.0
    # Legacy fields still have their original per-run meaning.
    assert result["abstention_correct"] == 1.0
    assert result["extraction_claim_coverage"] == 0.25


def test_temporal_label_and_unsupported_rate_require_real_denominators():
    state_case = next(case for case in cases() if case.id == "current-0")
    result = score_judgments(
        (state_case,),
        [
            {
                "case_id": state_case.id,
                "reviewers": [{"reviewer_id": "a"}, {"reviewer_id": "b"}],
                "adjudicated": {
                    "reviewer_id": "c",
                    "matched_gold_keys": [],
                    "unsupported_claims": 2,
                    "temporal_correct": False,
                },
            }
        ],
    )
    assert result["metrics"]["temporal_correct"]["denominator"] == 1
    assert result["metrics"]["temporal_correct"]["numerator"] == 0
    assert result["metrics"]["unsupported_extracted_claim_rate"]["value"] is None
    assert result["metrics"]["unsupported_extracted_claim_rate"]["missing_labels"] == 1
    assert result["unsupported_claims_total"] == 2

    invalid = {
        "case_id": state_case.id,
        "reviewers": [{"reviewer_id": "a"}, {"reviewer_id": "b"}],
        "adjudicated": {
            "reviewer_id": "c",
            "matched_gold_keys": [state_case.claims[0].key],
            "unsupported_claims": 1,
            "extracted_claims_count": 1,
        },
    }
    with pytest.raises(ValueError, match="smaller than classified"):
        score_judgments((state_case,), [invalid])


def test_unavailable_source_does_not_count_as_missed_gold_claims():
    case = next(case for case in cases() if case.id == "decision-0")
    result = score_judgments(
        (case,),
        [
            {
                "case_id": case.id,
                "reviewers": [{"reviewer_id": "a"}, {"reviewer_id": "b"}],
                "adjudicated": {
                    "reviewer_id": "c",
                    "source_outcome": "unavailable",
                    "matched_gold_keys": [],
                    "unsupported_claims": 0,
                    "extracted_claims_count": 0,
                },
            }
        ],
    )
    coverage = result["metrics"]["extraction_claim_coverage"]
    assert coverage["denominator"] == 0
    assert coverage["missing_labels"] == 0
    assert coverage["value"] is None
    assert result["source_outcomes"]["unavailable"] == 1
    assert result["metrics"]["source_processing_coverage"]["denominator"] == 0
