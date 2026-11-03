"""Score human judgments without treating absent labels as observed failures.

The scorer validates card shape, not reviewer independence or the truth of a label.
Only adjudicated labels enter outcome metrics; initial reviews enter agreement metrics.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .corpus import Case


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_id: str = Field(min_length=1, max_length=80)
    source_outcome: Literal["reviewed", "partial", "unprocessed", "unavailable"] | None = None
    matched_gold_keys: list[str] | None = None
    unsupported_claims: int | None = Field(default=None, ge=0)
    extracted_claims_count: int | None = Field(default=None, ge=0)
    retrieved_gold_keys: list[str] | None = None
    temporal_correct: bool | None = None
    answer_correct: bool | None = None
    grounded: bool | None = None
    citations_correct: bool | None = None
    abstained: bool | None = None


class CaseJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    run_id: str = Field(default="default", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    reviewers: list[Review] = Field(min_length=2, max_length=2)
    adjudicated: Review

    @model_validator(mode="after")
    def distinct_reviewers(self) -> "CaseJudgment":
        if self.reviewers[0].reviewer_id == self.reviewers[1].reviewer_id:
            raise ValueError("two distinct reviewers are required")
        return self


@dataclass
class Metric:
    numerator: int = 0
    denominator: int = 0
    eligible_runs: int = 0
    labeled_runs: int = 0
    missing_labels: int = 0

    def add(self, numerator: int | None, denominator: int = 1) -> None:
        self.eligible_runs += 1
        if numerator is None:
            self.missing_labels += 1
        else:
            self.labeled_runs += 1
            self.numerator += numerator
            self.denominator += denominator

    def report(self) -> dict[str, int | float | None]:
        return {
            "numerator": self.numerator,
            "denominator": self.denominator,
            "eligible_runs": self.eligible_runs,
            "labeled_runs": self.labeled_runs,
            "missing_labels": self.missing_labels,
            "value": self.numerator / self.denominator if self.denominator else None,
        }


def score_judgments(cases: tuple[Case, ...], raw: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {case.id: case for case in cases}
    seen: set[tuple[str, str]] = set()
    runs_per_case: Counter[str] = Counter()
    metrics: dict[str, Metric] = defaultdict(Metric)
    split_metrics: dict[str, dict[str, Metric]] = defaultdict(lambda: defaultdict(Metric))
    # Old flat fields retain their earlier per-run-average meaning.
    legacy: dict[str, list[float]] = defaultdict(list)
    legacy_by_split: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    agreement: list[float] = []
    source_agreement: list[float] = []
    source_outcomes: Counter[str] = Counter()
    unsupported_total = 0
    unsupported_labeled_runs = 0
    unsupported_missing_labels = 0
    unsupported_by_split: dict[str, dict[str, int]] = defaultdict(
        lambda: {"count": 0, "labeled_runs": 0, "missing_labels": 0}
    )

    def record(split: str, name: str, numerator: int | None, denominator: int = 1) -> None:
        metrics[name].add(numerator, denominator)
        split_metrics[split][name].add(numerator, denominator)

    def old_record(split: str, name: str, value: float) -> None:
        legacy[name].append(value)
        legacy_by_split[split][name].append(value)

    for raw_item in raw:
        item = CaseJudgment.model_validate(raw_item)
        if item.case_id not in by_id or (item.case_id, item.run_id) in seen:
            raise ValueError("unknown or repeated case/run ID")
        seen.add((item.case_id, item.run_id))
        runs_per_case[item.case_id] += 1
        case = by_id[item.case_id]
        expected = {claim.key for claim in case.claims}
        relevant = set(case.relevant)
        for review in [*item.reviewers, item.adjudicated]:
            if review.matched_gold_keys is not None:
                if len(review.matched_gold_keys) != len(set(review.matched_gold_keys)):
                    raise ValueError("matched gold keys must be unique")
                if not set(review.matched_gold_keys) <= expected:
                    raise ValueError("a matched key is not in this case's independent gold card")
            if (review.matched_gold_keys is None) != (review.unsupported_claims is None):
                raise ValueError(
                    "extraction match keys and unsupported count must be supplied together"
                )
            if review.extracted_claims_count is not None:
                if review.unsupported_claims is None:
                    raise ValueError("extracted claim count requires extraction labels")
                if review.extracted_claims_count < (
                    len(review.matched_gold_keys or []) + review.unsupported_claims
                ):
                    raise ValueError("extracted claim count is smaller than classified claims")
            if review.retrieved_gold_keys is not None:
                if len(review.retrieved_gold_keys) != len(set(review.retrieved_gold_keys)):
                    raise ValueError("retrieved gold keys must be unique")
                if not set(review.retrieved_gold_keys) <= relevant:
                    raise ValueError("retrieved key is not relevant to this case")
            if review.temporal_correct is not None and case.state is None:
                raise ValueError("temporal label requires a state case")
            if not case.requires_abstention and review.abstained and review.answer_correct:
                raise ValueError("an answerable case cannot be correct and abstained")

        first, second = item.reviewers
        if (
            first.matched_gold_keys is not None
            and second.matched_gold_keys is not None
            and first.answer_correct is not None
            and second.answer_correct is not None
        ):
            agreement.append(
                float(
                    set(first.matched_gold_keys) == set(second.matched_gold_keys)
                    and first.answer_correct == second.answer_correct
                )
            )
        if first.source_outcome is not None and second.source_outcome is not None:
            source_agreement.append(float(first.source_outcome == second.source_outcome))

        final = item.adjudicated
        split = case.split
        if final.source_outcome is None:
            record(split, "source_processing_coverage", None)
        else:
            source_outcomes[final.source_outcome] += 1
            if final.source_outcome != "unavailable":
                covered = int(final.source_outcome == "reviewed")
                record(split, "source_processing_coverage", covered)
                old_record(split, "source_processing_coverage", float(covered))

        # An explicitly unavailable source supplies no fair extraction denominator.
        if expected and final.source_outcome != "unavailable":
            if final.matched_gold_keys is None:
                record(split, "extraction_claim_coverage", None)
            else:
                matched = len(final.matched_gold_keys)
                record(split, "extraction_claim_coverage", matched, len(expected))
                old_record(split, "extraction_coverage", matched / len(expected))

        if final.unsupported_claims is None:
            unsupported_missing_labels += 1
            unsupported_by_split[split]["missing_labels"] += 1
        else:
            unsupported_total += final.unsupported_claims
            unsupported_labeled_runs += 1
            unsupported_by_split[split]["count"] += final.unsupported_claims
            unsupported_by_split[split]["labeled_runs"] += 1
        if final.extracted_claims_count is None:
            record(split, "unsupported_extracted_claim_rate", None)
        else:
            record(
                split,
                "unsupported_extracted_claim_rate",
                final.unsupported_claims,
                final.extracted_claims_count,
            )

        if relevant:
            record(
                split,
                "retrieval_recall",
                len(final.retrieved_gold_keys) if final.retrieved_gold_keys is not None else None,
                len(relevant),
            )
        if case.state is not None:
            record(
                split,
                "temporal_correct",
                int(final.temporal_correct) if final.temporal_correct is not None else None,
            )
        for name in ("answer_correct", "grounded", "citations_correct"):
            label = getattr(final, name)
            record(split, name, int(label) if label is not None else None)
            if label is not None:
                old_record(split, name, float(label))
        if not case.requires_abstention:
            record(
                split,
                "answerable_answer_correct",
                int(final.answer_correct) if final.answer_correct is not None else None,
            )
            record(
                split,
                "incorrect_abstention",
                int(final.abstained) if final.abstained is not None else None,
            )
        else:
            record(
                split,
                "correct_abstention",
                int(final.abstained) if final.abstained is not None else None,
            )
            if final.abstained is not None:
                old_record(split, "abstention_correct", float(final.abstained))

    def average(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    def legacy_value(name: str) -> float | None:
        return average(legacy[name])

    metric_names = (
        "source_processing_coverage",
        "extraction_claim_coverage",
        "unsupported_extracted_claim_rate",
        "retrieval_recall",
        "temporal_correct",
        "answer_correct",
        "answerable_answer_correct",
        "grounded",
        "citations_correct",
        "correct_abstention",
        "incorrect_abstention",
    )
    for name in metric_names:
        metrics[name]
    for split in sorted({case.split for case in cases}):
        for name in metric_names:
            split_metrics[split][name]
        unsupported_by_split[split]

    return {
        "schema_version": 2,
        "judged_cases": len(runs_per_case),
        "judged_runs": len(seen),
        "runs_per_case_min": min(runs_per_case.values(), default=0),
        "runs_per_case_max": max(runs_per_case.values(), default=0),
        "balanced_runs": len(set(runs_per_case.values())) <= 1,
        "coverage_cases": len(legacy["extraction_coverage"]),
        "extraction_claim_coverage": legacy_value("extraction_coverage"),
        "source_outcomes": {
            outcome: source_outcomes[outcome]
            for outcome in ("reviewed", "partial", "unprocessed", "unavailable")
        },
        "source_processing_cases": metrics["source_processing_coverage"].denominator,
        "source_processing_coverage": metrics["source_processing_coverage"].report()["value"],
        "source_agreement_cases": len(source_agreement),
        "source_agreement": average(source_agreement),
        "unsupported_claims_total": unsupported_total,
        "unsupported_claims_labeled_runs": unsupported_labeled_runs,
        "unsupported_claims_missing_labels": unsupported_missing_labels,
        "unsupported_claims_by_split": dict(sorted(unsupported_by_split.items())),
        "answer_cases": len(legacy["answer_correct"]),
        "answer_correct": legacy_value("answer_correct"),
        "grounded": legacy_value("grounded"),
        "citations_correct": legacy_value("citations_correct"),
        "missing_answer_cases": len(legacy["abstention_correct"]),
        "abstention_correct": legacy_value("abstention_correct"),
        "full_agreement_cases": len(agreement),
        "full_agreement": average(agreement),
        "metrics": {name: metric.report() for name, metric in sorted(metrics.items())},
        "metrics_by_split": {
            split: {name: metric.report() for name, metric in sorted(group.items())}
            for split, group in sorted(split_metrics.items())
        },
        "by_split": {
            split: {name: average(values) for name, values in sorted(group.items())}
            for split, group in sorted(legacy_by_split.items())
        },
    }
