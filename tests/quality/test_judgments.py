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
