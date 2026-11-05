"""Score recorded solo or independent judgments without inventing missing labels.

The scorer validates card shape, not reviewer independence or the truth of a label.
Only effective solo or adjudicated independent labels enter outcome metrics.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .client_packets import build_client_packets
from .corpus import Case
from .evaluation import (
    AutomatedCheck,
    ClientRun,
    EvaluationMode,
    Identifier,
    Provenance,
    RetrievalMode,
    validate_truth_audits,
)

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

    reviewer_id: Identifier
    provenance: Provenance | None = None
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
        if self.reviewers[0].reviewer_id.casefold() == self.reviewers[1].reviewer_id.casefold():
            raise ValueError("two distinct reviewers are required")
        if any(
            r.provenance is not None and r.provenance.kind != "human"
            for r in [*self.reviewers, self.adjudicated]
        ):
            raise ValueError("independent review cannot substitute machine judgments for humans")
        return self


class RecordedReview(Review):
    provenance: Provenance

    @model_validator(mode="after")
    def mechanical_labels_only(self) -> "RecordedReview":
        if self.provenance.kind == "deterministic" and any(
            getattr(self, field) is not None
            for field in (
                "source_outcome",
                "matched_gold_keys",
                "unsupported_claims",
                "extracted_claims_count",
                "answer_correct",
                "grounded",
                "citations_correct",
                "abstained",
                "unresolved_handling_correct",
            )
        ):
            raise ValueError("deterministic checks cannot certify semantic review labels")
        return self


class SoloCaseJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    run_id: str = Field(default="default", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    review: RecordedReview
    checks: list[AutomatedCheck] = Field(default_factory=list, max_length=100)
    needs_manual_review: bool = False
    manual_review: RecordedReview | None = None

    @model_validator(mode="after")
    def human_override(self) -> "SoloCaseJudgment":
        if self.manual_review is not None and self.manual_review.provenance.kind != "human":
            raise ValueError("manual review must record a human evaluator")
        if len({check.name for check in self.checks}) != len(self.checks):
            raise ValueError("automated check names must be unique within a run")
        return self

    def final_review(self) -> RecordedReview:
        return self.manual_review or self.review


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
    evaluation_mode: EvaluationMode = "independent_review",
    retrieval_mode: RetrievalMode = "lexical_only",
    truth_audits: list[dict[str, Any]] | None = None,
    client_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if evaluation_mode not in {"solo_operator", "independent_review"}:
        raise ValueError("unknown evaluation mode")
    if retrieval_mode not in {"lexical_only", "hybrid"}:
        raise ValueError("unknown retrieval mode")
    by_id = {case.id: case for case in cases}
    observed: set[tuple[str, str]] = set()
    parsed: list[CaseJudgment | SoloCaseJudgment] = []
    for raw_item in raw:
        item = (
            SoloCaseJudgment.model_validate(raw_item)
            if evaluation_mode == "solo_operator"
            else CaseJudgment.model_validate(raw_item)
        )
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
    kind_metrics: dict[str, dict[str, Metric]] = defaultdict(lambda: defaultdict(Metric))
    provenance_counts: Counter[str] = Counter()
    effective_kinds: Counter[str] = Counter()
    pending_manual = 0
    checks_passed = checks_failed = manual_reviews = 0
    active_kind = "unrecorded"
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
        kind_metrics[active_kind][name].add(numerator, denominator)

    def old_record(split: str, name: str, value: float) -> None:
        legacy[name].append(value)
        legacy_by_split[split][name].append(value)

    for item in scored:
        runs_per_case[item.case_id] += 1
        case = by_id[item.case_id]
        expected = {claim.key for claim in case.claims}
        relevant = set(case.relevant)
        reviews: list[Review] = (
            [*item.reviewers, item.adjudicated]
            if isinstance(item, CaseJudgment)
            else [item.review, *([item.manual_review] if item.manual_review else [])]
        )
        for review in reviews:
            provenance_counts[review.provenance.kind if review.provenance else "unrecorded"] += 1
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

        if isinstance(item, CaseJudgment):
            first, second = item.reviewers
        else:
            first = second = None
            checks_passed += sum(check.passed for check in item.checks)
            checks_failed += sum(not check.passed for check in item.checks)
            manual_reviews += item.manual_review is not None
            pending_manual += item.needs_manual_review and item.manual_review is None
        if (
            first is not None
            and second is not None
            and (
                first.matched_gold_keys is not None
                and second.matched_gold_keys is not None
                and first.answer_correct is not None
                and second.answer_correct is not None
            )
        ):
            agreement.append(
                float(
                    set(first.matched_gold_keys) == set(second.matched_gold_keys)
                    and first.answer_correct == second.answer_correct
                )
            )
        if (
            first is not None
            and second is not None
            and first.source_outcome is not None
            and second.source_outcome is not None
        ):
            source_agreement.append(float(first.source_outcome == second.source_outcome))

        final = item.adjudicated if isinstance(item, CaseJudgment) else item.final_review()
        active_kind = final.provenance.kind if final.provenance else "unrecorded"
        effective_kinds[active_kind] += 1
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

    selected_cases = tuple(
        case for case in cases if manifest_split is None or case.split == manifest_split
    )
    audits = validate_truth_audits(selected_cases, truth_audits or [], evaluation_mode)
    evidence: dict[tuple[str, str], ClientRun] = {}
    trace_digests: set[str] = set()
    for value in client_runs or []:
        receipt = ClientRun.model_validate(value)
        pair = (receipt.case_id, receipt.run_id)
        if pair in evidence:
            raise ValueError("duplicate client run receipt")
        if receipt.trace_sha256 in trace_digests:
            raise ValueError("a client trace cannot be reused as a different execution")
        if receipt.case_id not in by_id:
            raise ValueError("unknown client run case")
        if receipt.retrieval_mode != retrieval_mode:
            raise ValueError("cannot pool lexical-only and hybrid client evidence")
        if receipt.source_sha256 != sha256(by_id[receipt.case_id].source.encode()).hexdigest():
            raise ValueError("client source digest differs from the frozen corpus")
        if (
            packet_manifest is not None
            and receipt.corpus_sha256 != packet_manifest["corpus_sha256"]
        ):
            raise ValueError("client corpus digest differs from the packet manifest")
        evidence[pair] = receipt
        trace_digests.add(receipt.trace_sha256)
    verified_runs = 0
    for item in scored:
        receipt = evidence.get((item.case_id, item.run_id))
        recorded = (
            [*item.reviewers, item.adjudicated]
            if isinstance(item, CaseJudgment)
            else [item.review, *([item.manual_review] if item.manual_review else [])]
        )
        if receipt is not None:
            for review in recorded:
                if review.provenance and review.provenance.evidence_sha256 != receipt.trace_sha256:
                    raise ValueError("judgment provenance does not match its client trace")
            if isinstance(item, SoloCaseJudgment) and any(
                check.provenance.evidence_sha256 != receipt.trace_sha256 for check in item.checks
            ):
                raise ValueError("automated check provenance does not match its client trace")
            verified_runs += all(review.provenance is not None for review in recorded)
    expected_evidence = expected_pairs or set()
    evidence_complete = expected_pairs is not None and set(evidence) == expected_evidence
    labels_complete = run_status == "complete" and missing_metric_labels == 0

    return {
        "schema_version": 3,
        "evaluation_mode": evaluation_mode,
        "retrieval_mode": retrieval_mode,
        "validation_scope": "personal engineering"
        if evaluation_mode == "solo_operator"
        else "research protocol",
        "provenance": {
            "submitted_reviews_by_kind": dict(sorted(provenance_counts.items())),
            "effective_reviews_by_kind": dict(sorted(effective_kinds.items())),
            "manual_reviews": manual_reviews,
            "pending_manual_reviews": pending_manual,
            "independence_verified_by_scorer": False,
        },
        "automated_checks": {"passed": checks_passed, "failed": checks_failed},
        "truth_audit": audits,
        "client_evidence": {
            "observed_runs": len(evidence),
            "missing_runs": len(expected_evidence - evidence.keys())
            if expected_pairs is not None
            else None,
            "unexpected_runs": len(evidence.keys() - expected_evidence)
            if expected_pairs is not None
            else None,
            "trace_bound_judgments": verified_runs,
            "complete": evidence_complete,
            "authenticity": "operator_recorded_not_proven_by_schema",
        },
        "acceptance_evidence_complete": (
            labels_complete
            and audits["complete"]
            and evidence_complete
            and verified_runs == len(scored)
            and pending_manual == 0
        ),
        "quality_gate_status": "not_decided_by_scorer",
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
        "study_complete": labels_complete,
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
        "metrics_by_judgment_kind": {
            kind: {name: metric.report() for name, metric in sorted(group.items())}
            for kind, group in sorted(kind_metrics.items())
        },
        "metrics_by_split": {
            split: {name: metric.report() for name, metric in sorted(group.items())}
            for split, group in sorted(split_metrics.items())
        },
        "by_split": {
            split: {name: average(values) for name, values in sorted(group.items())}
            for split, group in sorted(legacy_by_split.items())
        },
    }
