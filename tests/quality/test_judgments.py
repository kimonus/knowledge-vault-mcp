"""Independent human-label scoring rejects self-inconsistent cards."""

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
