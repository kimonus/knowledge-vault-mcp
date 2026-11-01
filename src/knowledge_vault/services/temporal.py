"""Deterministic, conservative projections over immutable evidence, never insertion order."""

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from knowledge_vault.domain.models import AssertionInput, ReviewedTemporalAttachment
from knowledge_vault.domain.responses import (
    StateResolutionResponse,
    TemporalAttachmentResponse,
    TemporalDiagnosticsResponse,
    TemporalEvidenceView,
)
from knowledge_vault.domain.temporal import Precision, ResolutionRequest, TemporalInput
from knowledge_vault.observability.metrics import TEMPORAL_RESOLUTIONS
from knowledge_vault.persistence.tables import (
    AssertionRow,
    TemporalEvidenceRow,
    TemporalRetirementRow,
)
from knowledge_vault.services.errors import ConflictError, InvalidRequestError, NotFoundError

MAX_RESOLUTION_EVIDENCE = 1000


def window(value: datetime | None, precision: Precision) -> tuple[datetime, datetime] | None:
    """Half-open uncertainty window; an instant has equal endpoints."""
    if value is None or precision == "unknown":
        return None
    value = value.astimezone(UTC)
    if precision == "instant":
        return value, value
    start = value.replace(hour=0, minute=0, second=0, microsecond=0)
    if precision == "day":
        return start, start + timedelta(days=1)
    if precision == "month":
        start = start.replace(day=1)
        end = (
            start.replace(year=start.year + 1, month=1)
            if start.month == 12
            else start.replace(month=start.month + 1)
        )
        return start, end
    start = start.replace(month=1, day=1)
    return start, start.replace(year=start.year + 1)


@dataclass(frozen=True)
class Evidence:
    key: str
    assertion_id: UUID
    recorded_at: datetime
    assertion: AssertionInput
    retired: bool = False


@dataclass(frozen=True)
class _State:
    evidence: Evidence
    value: str
    mode: str
    anchor: tuple[datetime, datetime] | None
    basis: Literal["effective", "observed", "unknown"]
    end: tuple[datetime, datetime] | None
    reliable: bool
    strength: str


def _qualification(item: AssertionInput) -> TemporalInput:
    if item.temporal is None:
        raise InvalidRequestError("temporal evidence requires structured metadata")
    return item.temporal


def _state(evidence: Evidence) -> _State:
    item = evidence.assertion
    temporal = _qualification(item)
    effective = window(item.valid_from, temporal.valid_from_precision)
    observed = window(item.observed_at, temporal.observed_precision)
    reviewed_history = (
        item.status == "superseded"
        and temporal.legacy_supersession_reason == "historical_transition"
        and temporal.transition_assertion_id is not None
        and temporal.mode == "historical"
        and item.valid_from is not None
        and item.valid_to is not None
        and effective is not None
        and window(item.valid_to, temporal.valid_to_precision) is not None
        and item.evidence_state == "verified"
        and bool(item.sources)
        and temporal.observation_basis == "source"
    )
    return _State(
        evidence=evidence,
        value=temporal.value,
        mode=temporal.mode,
        anchor=effective or observed,
        basis="effective" if effective else "observed" if observed else "unknown",
        end=window(item.valid_to, temporal.valid_to_precision),
        reliable=(
            item.evidence_state in {"reported", "verified"}
            and (item.status == "current" or reviewed_history)
            and item.confidence > 0
            and item.origin in {"user", "joint", "external_source", "artifact"}
        ),
        strength=item.evidence_state,
    )


def _strictly_after(new: _State, old: _State) -> bool:
    if not new.anchor or not old.anchor:
        return False
    return new.anchor[0] >= old.anchor[1] and new.anchor[0] > old.anchor[0]


def _dominates(new: _State, old: _State) -> bool:
    if not new.reliable or not old.reliable or new.strength != old.strength:
        return False
    if not _strictly_after(new, old):
        return False
    # Explicitly conflicting validity intervals are evidence to review, not overwritten facts.
    if old.end and new.anchor and old.end[1] > new.anchor[0]:
        return False
    if new.mode == "change":
        return True
    # Independent snapshots only establish the latest observation; no validity is invented.
    return new.basis == old.basis == "observed" and old.end is None


def resolve_evidence(
    evidence: list[Evidence], request: ResolutionRequest, *, now: datetime
) -> StateResolutionResponse:
    """The whole logical result depends on persisted evidence, never list order or row IDs."""
    evidence = sorted(evidence, key=lambda e: e.key)
    by_key = {e.key: e for e in evidence}
    states = {e.key: _state(e) for e in evidence}
    corrected: set[str] = {e.key for e in evidence if e.retired}
    problems: set[str] = set()
    cached: dict[str, _State | None] = {}

    def correction_state(key: str, path: frozenset[str]) -> _State | None:
        if key in cached:
            return cached[key]
        if key in path:
            problems.add("cyclic_correction")
            return None
        current = states[key]
        temporal = _qualification(current.evidence.assertion)
        if temporal.mode != "correction":
            return current
        target = temporal.corrects_key
        if target is None or target not in by_key:
            problems.add("missing_correction_target")
            return None
        previous = correction_state(target, path | {key})
        if previous is None:
            return None
        # Uncertain corrections cannot silently invalidate supported knowledge.
        if not current.reliable:
            problems.add("unconfirmed_correction")
            return current
        corrected.add(target)
        item = current.evidence.assertion
        result = replace(
            current,
            mode=previous.mode,
            anchor=current.anchor if item.valid_from is not None else previous.anchor,
            basis=current.basis if item.valid_from is not None else previous.basis,
            end=current.end if item.valid_to is not None else previous.end,
        )
        cached[key] = result
        return result

    # Bound recursion independently of Python's recursion limit; long chains are unresolved.
    try:
        for key in states:
            if states[key].evidence.assertion.status == "superseded":
                continue
            result = correction_state(key, frozenset())
            if result is not None:
                states[key] = result
    except RecursionError:
        problems.add("correction_depth_exceeded")

    point = request.as_of or now
    candidates: list[_State] = []
    uncertain_bound = False
    dispositions: dict[
        str, Literal["supporting", "competing", "historical", "excluded", "corrected"]
    ] = {}
    for key, state in states.items():
        item = state.evidence.assertion
        if key in corrected or (item.status == "superseded" and not state.reliable):
            dispositions[key] = "corrected"
            continue
        if state.mode in {"proposal", "hypothesis", "correction"}:
            dispositions[key] = "excluded"
            continue
        if state.mode == "historical" and request.as_of is None:
            dispositions[key] = "historical"
            continue
        if state.end and state.end[1] <= point:
            dispositions[key] = "historical"
            continue
        if state.anchor and state.anchor[0] > point:
            dispositions[key] = "excluded"
            continue
        if request.as_of is not None:
            if (
                state.mode == "historical"
                and state.end is None
                and (state.anchor is None or state.anchor[1] < point)
            ):
                uncertain_bound = True
            if not state.anchor:
                uncertain_bound = True
            elif state.basis == "observed":
                # An observation is evidence for its precision window, not an open validity span.
                start, end = state.anchor
                if not (start == point or start <= point < end):
                    dispositions[key] = "historical"
                    continue
            elif state.anchor[0] <= point < state.anchor[1]:
                uncertain_bound = True
        if state.end and state.end[0] <= point < state.end[1]:
            uncertain_bound = True
        # Explicit unknown-precision bounds must not masquerade as unbounded applicability.
        temporal = _qualification(item)
        if (item.valid_from and temporal.valid_from_precision == "unknown") or (
            item.valid_to and temporal.valid_to_precision == "unknown"
        ):
            uncertain_bound = True
        candidates.append(state)

    frontier = [s for s in candidates if not any(_dominates(other, s) for other in candidates)]
    values = sorted({s.value for s in frontier})
    reasons: set[str] = set(problems)
    if uncertain_bound:
        reasons.add("uncertain_validity")
    if frontier and not any(s.anchor for s in frontier):
        reasons.add("unknown_chronology")
    if not frontier:
        outcome = "ambiguous" if problems or uncertain_bound else "unknown"
        reasons.add("no_applicable_confirmed_state")
    elif len(values) == 1 and all(s.reliable for s in frontier) and not reasons:
        outcome = "resolved"
        reasons.add(
            "latest_observed_state"
            if any(s.basis == "observed" for s in frontier)
            else "explicit_validity"
            if all(s.anchor for s in frontier)
            else "consistent_value_with_partial_chronology"
        )
    else:
        outcome = "ambiguous"
        if len(values) > 1:
            if all(s.anchor and s.reliable for s in frontier):
                outcome = "conflict"
                reasons.add("incompatible_applicable_evidence")
            else:
                reasons.add("incompatible_evidence_without_reliable_chronology")
        else:
            reasons.add("insufficient_confirmation")
    supporting = sorted(s.evidence.key for s in frontier) if outcome == "resolved" else []
    competing = sorted(s.evidence.key for s in frontier) if outcome != "resolved" else []
    for s in candidates:
        dispositions[s.evidence.key] = (
            "supporting"
            if s.evidence.key in supporting
            else "competing"
            if s.evidence.key in competing
            else "historical"
        )
    # Stable key pagination is independent of database IDs, transaction times and import order.
    page = [e for e in evidence if not request.history_after or e.key > request.history_after]
    has_more = len(page) > request.history_limit
    page = page[: request.history_limit]
    history = []
    for e in page:
        anchor = states[e.key].anchor
        history.append(
            TemporalEvidenceView(
                evidence_key=e.key,
                assertion_id=e.assertion_id,
                recorded_at=e.recorded_at,
                assertion=e.assertion,
                disposition=dispositions.get(e.key, "excluded"),
                ordering_at=anchor[0] if anchor else None,
                ordering_basis=states[e.key].basis,
            )
        )
    return StateResolutionResponse(
        identity=request.identity,
        as_of=request.as_of,
        known_at=request.known_at,
        outcome=outcome,
        values=values,
        supporting_keys=supporting,
        competing_keys=competing,
        reasons=sorted(reasons),
        evidence_total=len(evidence),
        history=history,
        next_history_after=page[-1].key if has_more else None,
    )


class TemporalService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def attach_reviewed(
        self, request: ReviewedTemporalAttachment
    ) -> TemporalAttachmentResponse:
        """Insert immutable evidence for a reviewed UUID; never touch the assertion row."""
        from knowledge_vault.services.maintenance import maintenance_lock

        item = request.evidence
        temporal = item.temporal
        if temporal is None:
            raise InvalidRequestError("structured temporal evidence is required")
        if item.evidence_state != "verified" or not item.last_verified_at:
            raise InvalidRequestError("reviewed evidence must be verified and dated")
        if not item.sources or temporal.observation_basis != "source":
            raise InvalidRequestError(
                "reviewed evidence requires a source URL and source observation basis"
            )
        if item.observed_at is None or temporal.observed_precision == "unknown":
            raise InvalidRequestError("reviewed evidence requires a dated source observation")
        if item.artifact_ids or item.supersedes_id or temporal.corrects_key:
            raise InvalidRequestError("attachment cannot alter artifacts or corrections")
        payload = item.model_dump(mode="json")
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        if request.expected_content_hash != item.stable_hash:
            raise ConflictError("reviewed evidence content hash does not match target hash")
        try:
            async with self._sessions.begin() as session:
                # Match flush's global order: content lock, then evidence-key lock,
                # then assertion rows. NOWAIT row locks prevent cycles with writers.
                for key in sorted(
                    (
                        f"content:{request.expected_content_hash}",
                        f"temporal:{temporal.evidence_key}",
                    )
                ):
                    await maintenance_lock(session, key)
                prior = await session.get(TemporalEvidenceRow, temporal.evidence_key)
                if prior:
                    if (
                        prior.assertion_id != request.assertion_id
                        or prior.payload_hash != payload_hash
                    ):
                        raise ConflictError(
                            "evidence_key already identifies a different target or payload"
                        )
                    return TemporalAttachmentResponse(
                        assertion_id=request.assertion_id,
                        evidence_key=temporal.evidence_key,
                        replayed=True,
                    )
                ids = {request.assertion_id}
                if temporal.transition_assertion_id:
                    ids.add(temporal.transition_assertion_id)
                locked = {}
                for assertion_id in sorted(ids):
                    locked[assertion_id] = await session.scalar(
                        select(AssertionRow)
                        .where(AssertionRow.id == assertion_id)
                        .with_for_update(read=True, nowait=True, of=AssertionRow)
                    )
                target = locked[request.assertion_id]
                successor = locked.get(temporal.transition_assertion_id)
                self._validate_new_attachment(request, target, successor)
                session.add(
                    TemporalEvidenceRow(
                        evidence_key=temporal.evidence_key,
                        assertion_id=request.assertion_id,
                        identity_hash=temporal.identity.digest,
                        payload_hash=payload_hash,
                        payload=payload,
                    )
                )
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) == "55P03":
                raise ConflictError(
                    "target or successor is being modified; retry attachment"
                ) from None
            raise
        return TemporalAttachmentResponse(
            assertion_id=request.assertion_id, evidence_key=temporal.evidence_key, replayed=False
        )

    @staticmethod
    def _validate_new_attachment(
        request: ReviewedTemporalAttachment,
        target: AssertionRow | None,
        successor: AssertionRow | None,
    ) -> None:
        item = request.evidence
        temporal = item.temporal
        if temporal is None:
            raise InvalidRequestError("structured temporal evidence is required")
        if target is None:
            raise NotFoundError("target assertion was not found")
        if target.content_hash != request.expected_content_hash:
            raise ConflictError("target assertion content hash changed")
        if (item.content, item.kind, item.origin, item.status) != (
            target.content,
            target.kind,
            target.origin,
            target.status,
        ):
            raise ConflictError(
                "reviewed evidence does not match target content, kind, origin and status"
            )
        identity_kinds = {
            "configuration": {"configuration"},
            "preference": {"preference"},
            "fact": {"user_fact", "external_fact"},
        }
        if item.kind not in identity_kinds[temporal.identity.state_type]:
            raise InvalidRequestError("identity type does not match target kind")
        if target.status == "superseded":
            if temporal.legacy_supersession_reason != "historical_transition":
                raise InvalidRequestError(
                    "superseded history requires a reviewed historical transition"
                )
            if temporal.mode != "historical" or not item.valid_from or not item.valid_to:
                raise InvalidRequestError(
                    "historical transition requires a bounded validity interval"
                )
            if (
                temporal.valid_from_precision == "unknown"
                or temporal.valid_to_precision == "unknown"
            ):
                raise InvalidRequestError("historical transition requires known bound precision")
            if successor is None or successor.supersedes_id != target.id:
                raise ConflictError("transition assertion must explicitly supersede target")
        elif temporal.transition_assertion_id or temporal.legacy_supersession_reason != "unknown":
            raise InvalidRequestError("transition review applies only to superseded assertions")

    async def resolve(self, request: ResolutionRequest) -> StateResolutionResponse:
        retired = exists().where(
            TemporalRetirementRow.evidence_key == TemporalEvidenceRow.evidence_key
        )
        if request.known_at:
            retired = retired.where(TemporalRetirementRow.retired_at <= request.known_at)
        statement = select(TemporalEvidenceRow, retired.label("retired")).where(
            TemporalEvidenceRow.identity_hash == request.identity.digest
        )
        if request.known_at:
            statement = statement.where(TemporalEvidenceRow.recorded_at <= request.known_at)
        async with self._sessions() as session:
            rows = list((await session.execute(statement.limit(MAX_RESOLUTION_EVIDENCE + 1))).all())
        if len(rows) > MAX_RESOLUTION_EVIDENCE:
            TEMPORAL_RESOLUTIONS.labels(outcome="overflow").inc()
            raise InvalidRequestError(
                "property exceeds the 1000-evidence resolution bound; no partial winner returned"
            )
        evidence = [
            Evidence(
                row.evidence_key,
                row.assertion_id,
                row.recorded_at,
                AssertionInput.model_validate(row.payload),
                retired=retired_claim,
            )
            for row, retired_claim in rows
        ]
        result = resolve_evidence(evidence, request, now=datetime.now(UTC))
        TEMPORAL_RESOLUTIONS.labels(outcome=result.outcome).inc()
        return result

    async def diagnostics(self) -> TemporalDiagnosticsResponse:
        # Diagnostics classify source metadata coverage, not the truth of a legacy status.
        count = select(func.count()).select_from(TemporalEvidenceRow).scalar_subquery()
        classified = select(TemporalEvidenceRow.assertion_id).distinct().subquery()
        async with self._sessions() as session:
            total = int(await session.scalar(select(func.count()).select_from(AssertionRow)) or 0)
            classified_total = int(
                await session.scalar(select(func.count()).select_from(classified)) or 0
            )
            fully = int(
                await session.scalar(
                    select(func.count())
                    .select_from(AssertionRow)
                    .where(
                        AssertionRow.id.in_(select(classified.c.assertion_id)),
                        AssertionRow.id.in_(
                            select(TemporalEvidenceRow.assertion_id).where(
                                TemporalEvidenceRow.payload["observed_at"].as_string().is_not(None),
                                TemporalEvidenceRow.payload["temporal"][
                                    "observed_precision"
                                ].as_string()
                                != "unknown",
                            )
                        ),
                    )
                )
                or 0
            )
            evidence_total = int(await session.scalar(select(count)) or 0)
        return TemporalDiagnosticsResponse(
            fully_classified=fully,
            partially_classified=classified_total - fully,
            unresolved=total - classified_total,
            evidence_total=evidence_total,
        )
