"""Explicit, bounded curation and aggregate usage. No inference changes stored claims."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import ColumnElement, delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from knowledge_vault.domain.maintenance import (
    ContextInput,
    LinkInput,
    MetadataUpdate,
    TopicAliasInput,
    UsageView,
)
from knowledge_vault.domain.models import AssertionInput
from knowledge_vault.domain.normalization import normalize_topic
from knowledge_vault.domain.responses import (
    AliasResponse,
    CandidatePair,
    ContextResponse,
    CurationResponse,
    LinkView,
    MutationResponse,
    RelatedResponse,
    ReviewItem,
    ReviewResponse,
)
from knowledge_vault.persistence.tables import (
    AssertionArtifactRow,
    AssertionLinkRow,
    AssertionRow,
    AssertionUsageRow,
    KnowledgeContextRow,
    MaintenanceEventRow,
    SourceRow,
    TopicAliasRow,
)
from knowledge_vault.services.errors import ConflictError, InvalidRequestError, NotFoundError


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


async def maintenance_lock(session: AsyncSession, key: str) -> None:
    # Stable cross-process lock, held only for this transaction. Never lock by Python's hash().
    number = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], signed=True)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": number})


async def validate_metadata_refs(session: AsyncSession, item: AssertionInput) -> None:
    if item.context_id and not await session.get(KnowledgeContextRow, item.context_id):
        raise InvalidRequestError("context_id was not found; create the context first")
    artifact_ids = [UUID(p.locator or "") for p in item.provenance if p.kind == "artifact"]
    if any(value not in item.artifact_ids for value in artifact_ids):
        raise InvalidRequestError("artifact provenance must also be listed in artifact_ids")


class MaintenanceService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def purge(self) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                delete(MaintenanceEventRow).where(
                    MaintenanceEventRow.expires_at <= datetime.now(UTC)
                )
            )

    async def update(self, principal: str, request: MetadataUpdate) -> MutationResponse:
        changes = request.changes.model_dump(mode="json", exclude_unset=True)
        if not changes:
            raise InvalidRequestError("provide at least one metadata change")
        key = _hash([principal, "metadata", request.idempotency_key])
        payload_hash = _hash(request.model_dump(mode="json", exclude={"dry_run"}))
        async with self._sessions.begin() as session:
            await maintenance_lock(session, key)
            reused = await session.scalar(
                select(MaintenanceEventRow)
                .where(
                    MaintenanceEventRow.key == key,
                    MaintenanceEventRow.expires_at > datetime.now(UTC),
                )
                .limit(1)
            )
            if reused and reused.payload_hash != payload_hash:
                raise ConflictError("idempotency key was used for a different metadata request")
            previous = await session.get(MaintenanceEventRow, (key, request.assertion_id))
            if previous and previous.expires_at > datetime.now(UTC):
                if previous.payload_hash != payload_hash:
                    raise ConflictError("idempotency key was used for different metadata changes")
                return MutationResponse(
                    assertion_id=request.assertion_id,
                    revision=previous.revision or 1,
                    changed_fields=previous.fields,
                    replayed=True,
                    dry_run=request.dry_run,
                )
            if previous:
                await session.delete(previous)
                await session.flush()
            row = await session.get(AssertionRow, request.assertion_id, with_for_update=True)
            if not row:
                raise NotFoundError("assertion was not found")
            if row.revision != request.expected_revision:
                raise ConflictError("assertion revision changed; fetch and review before retrying")
            if row.status == "superseded" and "status" in changes:
                raise InvalidRequestError(
                    "use explicit correction to reactivate superseded knowledge"
                )
            raw = {
                name: getattr(row, name)
                for name in AssertionInput.model_fields
                if hasattr(row, name) and name not in {"sources", "artifact_ids"}
            }
            raw["sources"] = [
                {
                    "url": s.url,
                    "title": s.title,
                    "publisher": s.publisher,
                    "retrieved_at": s.retrieved_at,
                }
                for s in row.sources
            ]
            raw["artifact_ids"] = list(
                await session.scalars(
                    select(AssertionArtifactRow.artifact_id).where(
                        AssertionArtifactRow.assertion_id == row.id
                    )
                )
            )
            raw.update(changes)
            # The existing supersession pointer describes history, not a new correction.
            raw["supersedes_id"] = None
            try:
                validated = AssertionInput.model_validate(raw)
            except ValidationError as exc:
                raise InvalidRequestError("metadata changes fail assertion validation") from exc
            await validate_metadata_refs(session, validated)
            if validated.workflow_state == "resolved" and not await session.scalar(
                select(AssertionLinkRow.source_id)
                .where(AssertionLinkRow.source_id == row.id, AssertionLinkRow.kind == "resolves")
                .limit(1)
            ):
                raise InvalidRequestError("a resolved question requires an explicit resolves link")
            result = MutationResponse(
                assertion_id=row.id,
                revision=row.revision + 1,
                changed_fields=sorted(changes),
                dry_run=request.dry_run,
            )
            if request.dry_run:
                return result
            for name in changes:
                if name != "sources":
                    value = getattr(validated, name)
                    if name == "provenance":
                        value = [p.model_dump(mode="json") for p in validated.provenance]
                    setattr(row, name, value)
            if "sources" in changes:
                wanted = {str(s.url): s for s in validated.sources}
                retained: list[SourceRow] = []
                existing = {s.url: s for s in row.sources}
                for url, source in wanted.items():
                    entry = existing.get(url) or SourceRow(url=url)
                    entry.title, entry.publisher = source.title, source.publisher
                    entry.retrieved_at = source.retrieved_at
                    retained.append(entry)
                row.sources = retained
            # Source-only edits still change the optimistic revision. The DB trigger agrees
            # with this value when another logical field changed as well.
            row.revision = result.revision
            row.updated_at = datetime.now(UTC)
            session.add(
                MaintenanceEventRow(
                    key=key,
                    assertion_id=row.id,
                    operation="metadata",
                    payload_hash=payload_hash,
                    revision=result.revision,
                    fields=result.changed_fields,
                    expires_at=datetime.now(UTC) + timedelta(days=30),
                )
            )
            return result

    async def context(self, value: ContextInput) -> ContextResponse:
        async with self._sessions.begin() as session:
            await maintenance_lock(session, "context:" + str(value.id))
            row = await session.get(KnowledgeContextRow, value.id)
            if row and (row.name != value.name or row.aliases != value.aliases):
                raise ConflictError("context ID already has different names")
            if not row:
                session.add(KnowledgeContextRow(**value.model_dump()))
        return ContextResponse(**value.model_dump())

    async def get_context(self, context_id: UUID) -> ContextResponse:
        async with self._sessions() as session:
            row = await session.get(KnowledgeContextRow, context_id)
            if not row:
                raise NotFoundError("context was not found")
            return ContextResponse(id=row.id, name=row.name, aliases=row.aliases)

    async def link(self, value: LinkInput) -> LinkView:
        if value.kind == "equivalent_to" and str(value.source_id) > str(value.target_id):
            value = value.model_copy(
                update={"source_id": value.target_id, "target_id": value.source_id}
            )
        async with self._sessions.begin() as session:
            # Serialize graph edits to prevent concurrent insertions forming a cycle.
            await maintenance_lock(session, "assertion-links")
            source = await session.get(AssertionRow, value.source_id, with_for_update=True)
            target = await session.get(AssertionRow, value.target_id, with_for_update=True)
            if not source or not target:
                raise NotFoundError("relationship endpoint was not found")
            old = await session.get(
                AssertionLinkRow, (value.source_id, value.target_id, value.kind)
            )
            if old:
                if old.position != value.position:
                    raise ConflictError("relationship already has a different position")
                return LinkView(**value.model_dump())
            if value.kind in {"part_of", "depends_on"}:
                # Recursive UNION deduplicates endpoints; this never loads claim text.
                reachable = await session.scalar(
                    text("""
                    WITH RECURSIVE paths(id) AS (
                      SELECT target_id FROM assertion_links WHERE source_id=:start AND kind=:kind
                      UNION SELECT l.target_id FROM assertion_links l
                        JOIN paths p ON l.source_id=p.id
                      WHERE l.kind=:kind
                    ) SELECT EXISTS(SELECT 1 FROM paths WHERE id=:finish)
                """),
                    {"start": value.target_id, "finish": value.source_id, "kind": value.kind},
                )
                if reachable:
                    raise ConflictError("relationship would create a cycle")
            if value.position is not None and await session.scalar(
                select(AssertionLinkRow.source_id).where(
                    AssertionLinkRow.target_id == value.target_id,
                    AssertionLinkRow.position == value.position,
                )
            ):
                raise ConflictError("part position is already occupied")
            if value.kind == "resolves":
                if source.kind != "open_question":
                    raise InvalidRequestError("resolves must point from a question to its answer")
                source.workflow_state = "resolved"
            # Link edits are visible to optimistic metadata writers as well.
            source.revision += 1
            target.revision += 1
            source.updated_at = target.updated_at = datetime.now(UTC)
            session.add(AssertionLinkRow(**value.model_dump()))
        return LinkView(**value.model_dump())

    async def related(self, assertion_id: UUID, limit: int = 50) -> RelatedResponse:
        if not 1 <= limit <= 100:
            raise InvalidRequestError("limit must be 1 through 100")
        async with self._sessions() as session:
            if not await session.get(AssertionRow, assertion_id):
                raise NotFoundError("assertion was not found")
            rows = list(
                await session.scalars(
                    select(AssertionLinkRow)
                    .where(
                        or_(
                            AssertionLinkRow.source_id == assertion_id,
                            AssertionLinkRow.target_id == assertion_id,
                        )
                    )
                    .order_by(
                        AssertionLinkRow.position.asc().nulls_last(),
                        AssertionLinkRow.source_id,
                        AssertionLinkRow.target_id,
                        AssertionLinkRow.kind,
                    )
                    .limit(limit + 1)
                )
            )
        return RelatedResponse(
            links=[
                LinkView.model_validate(
                    {
                        "source_id": r.source_id,
                        "target_id": r.target_id,
                        "kind": r.kind,
                        "position": r.position,
                    }
                )
                for r in rows[:limit]
            ],
            truncated=len(rows) > limit,
        )

    async def alias(self, value: TopicAliasInput) -> AliasResponse:
        alias, canonical = normalize_topic(value.alias), normalize_topic(value.canonical)
        if not alias or not canonical or alias == canonical:
            raise InvalidRequestError("alias and canonical topic must be different nonempty names")
        async with self._sessions.begin() as session:
            await maintenance_lock(session, "topic-aliases")
            rows = {
                a: c
                for a, c in (
                    await session.execute(select(TopicAliasRow.alias, TopicAliasRow.canonical))
                ).all()
            }
            if alias in rows and rows[alias] != canonical:
                raise ConflictError("alias already points to a different topic")
            if canonical in rows or alias in rows.values():
                raise ConflictError("alias chains are not allowed; use a canonical topic")
            if len(rows) >= 1000 and alias not in rows:
                raise ConflictError("topic alias limit reached")
            if alias not in rows:
                session.add(TopicAliasRow(alias=alias, canonical=canonical))
        return AliasResponse(aliases={alias: canonical})

    async def aliases(self) -> AliasResponse:
        async with self._sessions() as session:
            rows = await session.execute(
                select(TopicAliasRow.alias, TopicAliasRow.canonical)
                .order_by(TopicAliasRow.alias)
                .limit(1000)
            )
            return AliasResponse(aliases={a: c for a, c in rows.all()})

    async def review(
        self, *, limit: int = 50, after: UUID | None = None, context_id: UUID | None = None
    ) -> ReviewResponse:
        if not 1 <= limit <= 100:
            raise InvalidRequestError("limit must be 1 through 100")
        now = datetime.now(UTC)
        missing = (AssertionRow.kind == "external_fact") & (
            func.jsonb_array_length(AssertionRow.provenance) == 0
        )
        missing &= ~select(SourceRow.id).where(SourceRow.assertion_id == AssertionRow.id).exists()
        conditions = [
            or_(
                AssertionRow.review_after <= now,
                missing,
                AssertionRow.workflow_state == "unresolved",
                (AssertionRow.temporal_role == "ongoing") & AssertionRow.observed_at.is_(None),
            )
        ]
        if after:
            conditions.append(AssertionRow.id > after)
        if context_id:
            conditions.append(AssertionRow.context_id == context_id)
        async with self._sessions() as session:
            rows = list(
                await session.scalars(
                    select(AssertionRow)
                    .where(*conditions)
                    .order_by(AssertionRow.id)
                    .limit(limit + 1)
                )
            )
        items = []
        for row in rows[:limit]:
            reasons = []
            if row.review_after and row.review_after <= now:
                reasons.append("review_due")
            if row.kind == "external_fact" and not row.provenance and not row.sources:
                reasons.append("missing_provenance")
            if row.workflow_state == "unresolved":
                reasons.append("unresolved_question")
            if row.temporal_role == "ongoing" and not row.observed_at:
                reasons.append("missing_observation_date")
            items.append(ReviewItem(assertion_id=row.id, revision=row.revision, reasons=reasons))
        return ReviewResponse(
            items=items, next_after=rows[limit - 1].id if len(rows) > limit else None
        )

    async def candidates(
        self, *, context_id: UUID | None = None, topic: str | None = None, limit: int = 50
    ) -> CurationResponse:
        if not 1 <= limit <= 100 or not (context_id or topic):
            raise InvalidRequestError("provide a context or topic and limit 1 through 100")
        conditions: list[ColumnElement[bool]] = [
            AssertionRow.status.in_(["current", "uncertain", "disputed"])
        ]
        if context_id:
            conditions.append(AssertionRow.context_id == context_id)
        if topic:
            conditions.append(AssertionRow.topics.overlap([normalize_topic(topic)]))
        async with self._sessions() as session:
            rows = list(
                await session.scalars(
                    select(AssertionRow).where(*conditions).order_by(AssertionRow.id).limit(100)
                )
            )
        pairs = []
        for index, left in enumerate(rows):
            for right in rows[index + 1 :]:
                competing = (
                    left.context_id
                    and left.context_id == right.context_id
                    and left.facet
                    and left.facet == right.facet
                    and left.temporal_role == right.temporal_role == "ongoing"
                )
                disjoint = (
                    left.valid_to and right.valid_from and left.valid_to <= right.valid_from
                ) or (right.valid_to and left.valid_from and right.valid_to <= left.valid_from)
                if competing and not disjoint:
                    pairs.append(
                        CandidatePair(left_id=left.id, right_id=right.id, reason="competing_state")
                    )
                elif (
                    left.kind == right.kind
                    and SequenceMatcher(
                        None,
                        left.normalized_content[:2000],
                        right.normalized_content[:2000],
                        autojunk=False,
                    ).ratio()
                    >= 0.8
                ):
                    pairs.append(
                        CandidatePair(left_id=left.id, right_id=right.id, reason="similar_text")
                    )
                if len(pairs) == limit:
                    return CurationResponse(candidates=pairs, inspected=len(rows))
        return CurationResponse(candidates=pairs, inspected=len(rows))


class UsageService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record(
        self,
        ids: list[UUID],
        *,
        principal: str = "retrieval",
        event_id: UUID | None = None,
        operation: str = "search",
    ) -> int:
        ids = sorted(set(ids))
        if not ids:
            return 0
        if len(ids) > 100 or operation not in {"search", "fetch", "use"}:
            raise InvalidRequestError("invalid usage event")
        key = _hash([principal, operation, str(event_id)])
        payload_hash = _hash(ids)
        now = datetime.now(UTC)
        async with self._sessions.begin() as session:
            if operation != "use":
                await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
                await session.execute(text("SET LOCAL statement_timeout = '2s'"))
            if event_id is not None:
                await maintenance_lock(session, key)
                await session.execute(
                    delete(MaintenanceEventRow).where(
                        MaintenanceEventRow.key == key, MaintenanceEventRow.expires_at <= now
                    )
                )
                reused = await session.scalar(
                    select(MaintenanceEventRow).where(MaintenanceEventRow.key == key).limit(1)
                )
                if reused and reused.payload_hash != payload_hash:
                    raise ConflictError("event ID was used for a different assertion set")
            # Key-share locks protect against concurrent hard deletion without serializing reads.
            found = list(
                await session.scalars(
                    select(AssertionRow.id)
                    .where(AssertionRow.id.in_(ids))
                    .order_by(AssertionRow.id)
                    .with_for_update(read=True, key_share=True)
                )
            )
            if operation == "use" and found != ids:
                raise NotFoundError("one or more assertions were not found")
            if not found:
                return 0
            if event_id is not None:
                event = (
                    insert(MaintenanceEventRow)
                    .values(
                        [
                            {
                                "key": key,
                                "assertion_id": ident,
                                "operation": operation,
                                "payload_hash": payload_hash,
                                "revision": None,
                                "fields": [],
                                "expires_at": now + timedelta(hours=24),
                            }
                            for ident in found
                        ]
                    )
                    .on_conflict_do_nothing()
                    .returning(MaintenanceEventRow.assertion_id)
                )
                found = list(await session.scalars(event))
            if not found:
                return 0
            used, fetched = operation == "use", operation == "fetch"
            entry = insert(AssertionUsageRow).values(
                [
                    {
                        "assertion_id": ident,
                        "retrieval_count": 0 if used else 1,
                        "explicit_fetch_count": int(fetched),
                        "use_count": int(used),
                        "last_retrieved_at": None if used else now,
                        "last_explicit_fetch_at": now if fetched else None,
                        "last_used_at": now if used else None,
                    }
                    for ident in sorted(found)
                ]
            )
            updates: dict[str, object] = {}
            for counter, timestamp, enabled in (
                ("retrieval_count", "last_retrieved_at", not used),
                ("explicit_fetch_count", "last_explicit_fetch_at", fetched),
                ("use_count", "last_used_at", used),
            ):
                if enabled:
                    updates[counter] = getattr(AssertionUsageRow, counter) + 1
                    updates[timestamp] = func.greatest(getattr(AssertionUsageRow, timestamp), now)
            await session.execute(
                entry.on_conflict_do_update(index_elements=["assertion_id"], set_=updates)
            )
        return len(found)

    async def views(self, ids: list[UUID]) -> dict[UUID, UsageView]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(AssertionUsageRow).where(AssertionUsageRow.assertion_id.in_(ids))
            )
            return {r.assertion_id: UsageView.model_validate(r, from_attributes=True) for r in rows}
