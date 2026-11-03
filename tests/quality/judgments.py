"""Score human judgments without treating absent labels as observed failures.

The scorer validates card shape, not reviewer independence or the truth of a label.
Only adjudicated labels enter outcome metrics; initial reviews enter agreement metrics.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .client_packets import build_client_packets
from .corpus import Case

ResponseExpectation = Literal["definitive", "abstention", "unresolved"]


def response_expectation(case: Case) -> ResponseExpectation:
    """A conflict or unknown state needs an explanation, not a guessed value."""
    if case.state is not None and case.state.outcome in {"conflict", "ambiguous", "unknown"}:
        return "unresolved"
    if case.requires_abstention:
        return "abstention"
    return "definitive"


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_id: str = Field(min_length=1, max_length=80)
    source_outcome: Literal["reviewed", "partial", "unprocessed", "unavailable"] | None = None
    matched_gold_keys: list[str] | None = None
    unsupported_claims: int | None = Field(default=None, ge=0)
    extracted_claims_count: int | None = Field(default=None, ge=0)
    retrieved_gold_keys: list[str] | None = None
    temporal_correct: bool | None = None
    unresolved_handling_correct: bool | None = None
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


def _validated_packet_manifest(
    cases: tuple[Case, ...], packet_manifest: dict[str, Any]
) -> tuple[set[tuple[str, str]], list[tuple[str, str]], str]:
    """Accept only an unmodified generated packet set; never echo packet source text."""
    try:
        split = packet_manifest["split"]
        case_count = packet_manifest["case_count"]
        run_count = packet_manifest["run_count"]
        if (
            not isinstance(split, str)
            or not isinstance(case_count, int)
            or not isinstance(run_count, int)
            or case_count < 1
            or run_count < 1
            or run_count % case_count
        ):
            raise ValueError("invalid packet manifest totals")
        generated = build_client_packets(cases, split=split, repetitions=run_count // case_count)
    except (KeyError, TypeError, ValueError):
        raise ValueError("invalid generated packet manifest") from None
    if packet_manifest != generated:
        raise ValueError("packet manifest differs from the generated corpus packets")
    ordered = [(packet["case_id"], packet["run_id"]) for packet in generated["packets"]]
    return set(ordered), ordered, split


def score_judgments(
    cases: tuple[Case, ...],
    raw: list[dict[str, Any]],
    *,
    packet_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    by_id = {case.id: case for case in cases}
    observed: set[tuple[str, str]] = set()
    parsed: list[CaseJudgment] = []
    for raw_item in raw:
        item = CaseJudgment.model_validate(raw_item)
        pair = (item.case_id, item.run_id)
        if pair in observed:
            raise ValueError("repeated case/run ID")
        if item.case_id not in by_id and packet_manifest is None:
            raise ValueError("unknown case/run ID")
        observed.add(pair)
        parsed.append(item)
    if packet_manifest is None:
        expected_pairs: set[tuple[str, str]] | None = None
        ordered_pairs: list[tuple[str, str]] = []
        manifest_split: str | None = None
        scored = parsed
    else:
        expected_pairs, ordered_pairs, manifest_split = _validated_packet_manifest(
            cases, packet_manifest
        )
        # Extra executions are visible in completeness, but cannot bias planned metrics.
        scored = [item for item in parsed if (item.case_id, item.run_id) in expected_pairs]
    missing_pairs = expected_pairs - observed if expected_pairs is not None else set()
    unexpected_pairs = observed - expected_pairs if expected_pairs is not None else set()
    run_status = (
        "unchecked"
        if expected_pairs is None
        else "partial"
        if missing_pairs or unexpected_pairs
        else "complete"
    )
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

    for item in scored:
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
            if review.unresolved_handling_correct is not None and (
                response_expectation(case) != "unresolved"
            ):
                raise ValueError("unresolved handling label requires an unresolved state case")
            if (
                response_expectation(case) == "definitive"
                and review.abstained
                and review.answer_correct
            ):
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
        expectation = response_expectation(case)
        if expectation == "definitive":
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
        elif expectation == "abstention":
            record(
                split,
                "correct_abstention",
                int(final.abstained) if final.abstained is not None else None,
            )
            if final.abstained is not None:
                old_record(split, "abstention_correct", float(final.abstained))
        else:
            record(
                split,
                "unresolved_handling_correct",
                int(final.unresolved_handling_correct)
                if final.unresolved_handling_correct is not None
                else None,
            )

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
        "unresolved_handling_correct",
    )
    for name in metric_names:
        metrics[name]
    for split in sorted({case.split for case in cases}):
        for name in metric_names:
            split_metrics[split][name]
        unsupported_by_split[split]
    missing_metric_labels = sum(metric.missing_labels for metric in metrics.values())

    return {
        "schema_version": 2,
        "run_completeness": {
            "status": run_status,
            "manifest_split": manifest_split,
            "expected_runs": len(expected_pairs) if expected_pairs is not None else None,
            "observed_runs": len(observed),
            "missing_runs": len(missing_pairs) if expected_pairs is not None else None,
            "unexpected_runs": len(unexpected_pairs) if expected_pairs is not None else None,
            # Indices identify missing packets locally without putting source or IDs in a report.
            "missing_packet_indices": [
                index for index, pair in enumerate(ordered_pairs) if pair in missing_pairs
            ],
        },
        "study_complete": run_status == "complete" and missing_metric_labels == 0,
        "missing_metric_labels_total": missing_metric_labels,
        "judged_cases": len(runs_per_case),
        "judged_runs": len(scored),
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
