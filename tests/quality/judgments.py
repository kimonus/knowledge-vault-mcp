"""Score independently reviewed client extraction and answer observations.

This module accepts explicit human judgments. It never asks the vault to label its own
outputs, and it never stores raw conversations or answers in its aggregate report.
"""

from collections import Counter, defaultdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .corpus import Case


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_id: str = Field(min_length=1, max_length=80)
    source_outcome: Literal["reviewed", "partial", "unprocessed", "unavailable"] | None = None
    matched_gold_keys: list[str] | None = None
    unsupported_claims: int | None = Field(default=None, ge=0)
    answer_correct: bool | None = None
    grounded: bool | None = None
    citations_correct: bool | None = None
    abstained: bool | None = None


class CaseJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    reviewers: list[Review] = Field(min_length=2, max_length=2)
    adjudicated: Review

    @model_validator(mode="after")
    def distinct_reviewers(self) -> "CaseJudgment":
        if self.reviewers[0].reviewer_id == self.reviewers[1].reviewer_id:
            raise ValueError("two distinct reviewers are required")
        return self


def score_judgments(cases: tuple[Case, ...], raw: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {case.id: case for case in cases}
    seen: set[str] = set()
    extraction_coverage: list[float] = []
    unsupported: list[int] = []
    answer_correct: list[float] = []
    grounded: list[float] = []
    citations_correct: list[float] = []
    abstention_correct: list[float] = []
    agreement: list[float] = []
    source_agreement: list[float] = []
    source_outcomes: Counter[str] = Counter()
    by_split: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for raw_item in raw:
        item = CaseJudgment.model_validate(raw_item)
        if item.case_id not in by_id or item.case_id in seen:
            raise ValueError("unknown or repeated case ID")
        seen.add(item.case_id)
        case = by_id[item.case_id]
        expected = {claim.key for claim in case.claims}
        for review in [*item.reviewers, item.adjudicated]:
            if (
                review.matched_gold_keys is not None
                and not set(review.matched_gold_keys) <= expected
            ):
                raise ValueError("a matched key is not in this case's independent gold card")
            if (review.matched_gold_keys is None) != (review.unsupported_claims is None):
                raise ValueError(
                    "extraction match keys and unsupported count must be supplied together"
                )
        first, second = item.reviewers
        comparable = (
            first.matched_gold_keys is not None
            and second.matched_gold_keys is not None
            and first.answer_correct is not None
            and second.answer_correct is not None
        )
        if comparable:
            agreement.append(
                float(
                    set(first.matched_gold_keys or []) == set(second.matched_gold_keys or [])
                    and first.answer_correct == second.answer_correct
                )
            )
        if first.source_outcome is not None and second.source_outcome is not None:
            source_agreement.append(float(first.source_outcome == second.source_outcome))
        final = item.adjudicated
        if final.source_outcome is not None:
            source_outcomes[final.source_outcome] += 1
            if final.source_outcome != "unavailable":
                by_split[case.split]["source_processing_coverage"].append(
                    float(final.source_outcome == "reviewed")
                )
        if final.matched_gold_keys is not None and expected:
            coverage = len(set(final.matched_gold_keys)) / len(expected)
            extraction_coverage.append(coverage)
            unsupported.append(final.unsupported_claims or 0)
            by_split[case.split]["extraction_coverage"].append(coverage)
        elif final.unsupported_claims is not None:
            unsupported.append(final.unsupported_claims)
        for name, series in (
            ("answer_correct", answer_correct),
            ("grounded", grounded),
            ("citations_correct", citations_correct),
        ):
            value = getattr(final, name)
            if value is not None:
                series.append(float(value))
                by_split[case.split][name].append(float(value))
        if final.abstained is not None and case.requires_abstention:
            abstention_correct.append(float(final.abstained))
            by_split[case.split]["abstention_correct"].append(float(final.abstained))

    def average(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    source_processing_cases = sum(
        source_outcomes[outcome] for outcome in ("reviewed", "partial", "unprocessed")
    )
    return {
        "schema_version": 1,
        "judged_cases": len(seen),
        "coverage_cases": len(extraction_coverage),
        "extraction_claim_coverage": average(extraction_coverage),
        "source_outcomes": {
            outcome: source_outcomes[outcome]
            for outcome in ("reviewed", "partial", "unprocessed", "unavailable")
        },
        "source_processing_cases": source_processing_cases,
        "source_processing_coverage": (
            source_outcomes["reviewed"] / source_processing_cases
            if source_processing_cases
            else None
        ),
        "source_agreement_cases": len(source_agreement),
        "source_agreement": average(source_agreement),
        "unsupported_claims_total": sum(unsupported),
        "answer_cases": len(answer_correct),
        "answer_correct": average(answer_correct),
        "grounded": average(grounded),
        "citations_correct": average(citations_correct),
        "missing_answer_cases": len(abstention_correct),
        "abstention_correct": average(abstention_correct),
        "full_agreement_cases": len(agreement),
        "full_agreement": average(agreement),
        "by_split": {
            split: {metric: average(values) for metric, values in metrics.items()}
            for split, metrics in sorted(by_split.items())
        },
    }
