"""Recorded evaluation provenance, distinct from evidence of reviewer independence."""

from collections import Counter
from hashlib import sha256
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .corpus import Case

EvaluationMode = Literal["solo_operator", "independent_review"]
RetrievalMode = Literal["lexical_only", "hybrid"]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
JudgmentKind = Literal["human", "deterministic", "model"]


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: JudgmentKind
    method: Identifier
    recorded_at: AwareDatetime
    evidence_sha256: Digest


class AutomatedCheck(BaseModel):
    """A mechanical check is never a semantic answer/grounding judgment."""

    model_config = ConfigDict(extra="forbid")

    name: Identifier
    passed: bool
    provenance: Provenance

    @model_validator(mode="after")
    def deterministic(self) -> "AutomatedCheck":
        if self.provenance.kind != "deterministic":
            raise ValueError("automated checks require deterministic provenance")
        return self


class TruthAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: Identifier
    audit_id: Identifier
    reviewer_id: Identifier
    source_sha256: Digest
    artifact_sha256: Digest
    provenance: Provenance
    verdict: Literal["accepted", "needs_revision", "unjudged"]
    independent: bool = False
    before_client_output: bool = False
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def honest_independence(self) -> "TruthAudit":
        if self.provenance.kind == "deterministic" and self.verdict == "accepted":
            raise ValueError("mechanical checks cannot accept a semantic truth card")
        if self.independent and self.provenance.kind != "human":
            raise ValueError("machine truth audits cannot claim independent human review")
        if self.provenance.evidence_sha256 != self.source_sha256:
            raise ValueError("truth audit provenance must identify its source digest")
        if self.verdict == "needs_revision" and not self.notes.strip():
            raise ValueError("a truth-card revision needs an explanation")
        return self


class ClientRun(BaseModel):
    """Operator-local receipt; validation does not prove that its author ran an assistant."""

    model_config = ConfigDict(extra="forbid")

    case_id: Identifier
    run_id: Identifier
    kind: Literal["assistant_client"]
    client_name: Identifier
    client_version: Identifier
    client_model: Identifier
    server_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    schema_revision: Identifier
    retrieval_mode: RetrievalMode
    embedding_model: str | None = None
    corpus_sha256: Digest
    source_sha256: Digest
    trace_sha256: Digest
    trace_ref: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def separate_arms(self) -> "ClientRun":
        if self.retrieval_mode == "lexical_only" and self.embedding_model is not None:
            raise ValueError("lexical-only evidence must have a null embedding model")
        if self.retrieval_mode == "hybrid" and not self.embedding_model:
            raise ValueError("hybrid evidence must identify its embedding model")
        return self


def validate_truth_audits(
    cases: tuple[Case, ...], raw: list[dict[str, Any]], mode: EvaluationMode
) -> dict[str, Any]:
    """Duplicate files/receipts cannot satisfy a second review, even if IDs are edited."""
    by_id = {case.id: case for case in cases}
    grouped: dict[str, list[TruthAudit]] = {}
    audit_ids: set[str] = set()
    artifacts: set[tuple[str, str]] = set()
    reviewers: set[tuple[str, str]] = set()
    kinds: Counter[str] = Counter()
    before_output = 0
    for value in raw:
        audit = TruthAudit.model_validate(value)
        if audit.case_id not in by_id:
            raise ValueError("unknown truth-audit case")
        if audit.source_sha256 != sha256(by_id[audit.case_id].source.encode()).hexdigest():
            raise ValueError("truth audit source digest differs from the frozen corpus")
        reviewer = (audit.case_id, audit.reviewer_id.casefold())
        artifact = (audit.case_id, audit.artifact_sha256)
        if audit.audit_id in audit_ids or artifact in artifacts or reviewer in reviewers:
            raise ValueError("duplicate truth audit, reviewer, or copied audit artifact")
        if mode == "independent_review" and (
            audit.provenance.kind != "human"
            or not audit.independent
            or not audit.before_client_output
        ):
            raise ValueError("independent review requires two independently recorded human audits")
        audit_ids.add(audit.audit_id)
        artifacts.add(artifact)
        reviewers.add(reviewer)
        grouped.setdefault(audit.case_id, []).append(audit)
        kinds[audit.provenance.kind] += 1
        before_output += audit.before_client_output
    required = 2 if mode == "independent_review" else 1
    complete = sum(
        len(grouped.get(case.id, [])) >= required
        and all(a.verdict == "accepted" for a in grouped[case.id])
        for case in cases
    )
    return {
        "evaluation_mode": mode,
        "required_audits_per_case": required,
        "expected_cases": len(cases),
        "complete_cases": complete,
        "missing_or_unresolved_cases": len(cases) - complete,
        "by_kind": dict(sorted(kinds.items())),
        "before_client_output": before_output,
        "retrospective_or_unrecorded_timing": sum(kinds.values()) - before_output,
        "complete": complete == len(cases),
        "independence": "operator_attested" if mode == "independent_review" else "not_claimed",
    }
