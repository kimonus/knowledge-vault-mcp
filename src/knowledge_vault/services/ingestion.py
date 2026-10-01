import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from knowledge_vault.config import Settings
from knowledge_vault.domain.enums import AssertionStatus, BatchState, EmbeddingState
from knowledge_vault.domain.models import (
    AssertionInput,
    CommitCounts,
    CommitResult,
    RejectedItem,
)
from knowledge_vault.observability.metrics import ASSERTION_OUTCOMES, BATCH_TRANSITIONS
from knowledge_vault.persistence.tables import (
    AssertionRow,
    ConflictRow,
    EmbeddingJobRow,
    FlushBatchRow,
    FlushPartRow,
    SourceRow,
)
from knowledge_vault.services.conflicts import looks_contradictory
from knowledge_vault.services.errors import BatchIncompleteError, ConflictError, NotFoundError


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _idempotency_hash(principal_id: str, key: str) -> str:
    return hashlib.sha256(f"{principal_id}\0{key}".encode()).hexdigest()


class IngestionService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self._sessions = sessions
        self._settings = settings

    async def begin(
        self,
        principal_id: str,
        idempotency_key: str,
        declared_parts: int,
        declared_items: int,
    ) -> FlushBatchRow:
        if not idempotency_key or len(idempotency_key) > 200:
            raise ConflictError("idempotency_key must contain 1 through 200 characters")
        if not 1 <= declared_parts <= self._settings.max_parts:
            raise ConflictError(f"declared_parts must be between 1 and {self._settings.max_parts}")
        if not 1 <= declared_items <= self._settings.max_batch_items:
            raise ConflictError(
                f"declared_items must be between 1 and {self._settings.max_batch_items}"
            )
        key_hash = _idempotency_hash(principal_id, idempotency_key)
        async with self._sessions.begin() as session:
            existing = await session.scalar(
                select(FlushBatchRow).where(
                    FlushBatchRow.principal_id == principal_id,
                    FlushBatchRow.idempotency_hash == key_hash,
                )
            )
            if existing:
                if (
                    existing.declared_parts != declared_parts
                    or existing.declared_items != declared_items
                ):
                    raise ConflictError("idempotency key was already used with different totals")
                return existing
            batch = FlushBatchRow(
                principal_id=principal_id,
                idempotency_hash=key_hash,
                declared_parts=declared_parts,
                declared_items=declared_items,
                expires_at=datetime.now(UTC)
                + timedelta(seconds=self._settings.staging_ttl_seconds),
            )
            session.add(batch)
        BATCH_TRANSITIONS.labels(state=BatchState.OPEN).inc()
        return batch

    async def append(
        self,
        principal_id: str,
        batch_id: UUID,
        part_number: int,
        raw_items: list[dict[str, Any]],
    ) -> tuple[int, list[RejectedItem], bool]:
        if not 1 <= len(raw_items) <= self._settings.max_part_items:
            raise ConflictError(
                f"a part must contain 1 through {self._settings.max_part_items} items"
            )
        request_hash = _canonical_hash(raw_items)
        staged: list[dict[str, Any]] = []
        rejected: list[RejectedItem] = []
        for index, raw in enumerate(raw_items):
            try:
                item = AssertionInput.model_validate(raw)
                staged.append(item.model_dump(mode="json"))
            except ValidationError as exc:
                issue = exc.errors(include_input=False)[0]
                rejected_item = RejectedItem(
                    index=index,
                    code=str(issue["type"]),
                    message=str(issue["msg"]),
                )
                rejected.append(rejected_item)
                staged.append({"_rejected": rejected_item.model_dump(mode="json")})

        async with self._sessions.begin() as session:
            batch = await session.scalar(
                select(FlushBatchRow)
                .where(
                    FlushBatchRow.id == batch_id,
                    FlushBatchRow.principal_id == principal_id,
                )
                .with_for_update()
            )
            if not batch:
                raise NotFoundError("flush batch was not found")
            if batch.state != BatchState.OPEN:
                raise ConflictError(f"cannot append to batch in {batch.state} state")
            if batch.expires_at <= datetime.now(UTC):
                batch.state = BatchState.EXPIRED
                raise ConflictError("flush batch has expired")
            if not 1 <= part_number <= batch.declared_parts:
                raise ConflictError("part_number is outside the declared range")
            prior = await session.scalar(
                select(FlushPartRow).where(
                    FlushPartRow.batch_id == batch_id,
                    FlushPartRow.part_number == part_number,
                )
            )
            if prior:
                if prior.payload_hash != request_hash:
                    raise ConflictError("part number was already accepted with a different payload")
                prior_rejected = [
                    RejectedItem.model_validate(item["_rejected"])
                    for item in prior.payload
                    if "_rejected" in item
                ]
                return prior.item_count - len(prior_rejected), prior_rejected, True
            session.add(
                FlushPartRow(
                    batch_id=batch_id,
                    part_number=part_number,
                    payload_hash=request_hash,
                    item_count=len(raw_items),
                    payload=staged,
                )
            )
        return len(raw_items) - len(rejected), rejected, False

    async def commit(self, principal_id: str, batch_id: UUID) -> CommitResult:
        async with self._sessions.begin() as session:
            batch = await session.scalar(
                select(FlushBatchRow)
                .options(selectinload(FlushBatchRow.parts))
                .where(
                    FlushBatchRow.id == batch_id,
                    FlushBatchRow.principal_id == principal_id,
                )
                .with_for_update()
            )
            if not batch:
                raise NotFoundError("flush batch was not found")
            if batch.state == BatchState.COMMITTED and batch.result:
                return CommitResult.model_validate(batch.result)
            if batch.state != BatchState.OPEN:
                raise ConflictError(f"cannot commit batch in {batch.state} state")
            expected = set(range(1, batch.declared_parts + 1))
            received = {part.part_number for part in batch.parts}
            received_items = sum(part.item_count for part in batch.parts)
            if received != expected or received_items != batch.declared_items:
                missing = sorted(expected - received)
                raise BatchIncompleteError(
                    f"batch is incomplete: missing parts={missing}, "
                    f"received_items={received_items}, declared_items={batch.declared_items}"
                )

            counts = CommitCounts()
            assertion_ids: list[UUID] = []
            conflict_ids: list[UUID] = []
            rejected_items: list[RejectedItem] = []
            ordered_parts = sorted(batch.parts, key=lambda part: part.part_number)
            global_index = 0
            for part in ordered_parts:
                for raw in part.payload:
                    if "_rejected" in raw:
                        rejection = RejectedItem.model_validate(raw["_rejected"])
                        rejection.index = global_index
                        rejected_items.append(rejection)
                        counts.rejected += 1
                        global_index += 1
                        continue
                    item = AssertionInput.model_validate(raw)
                    assertion_id, outcome, conflicts = await self._apply_item(session, item)
                    assertion_ids.append(assertion_id)
                    setattr(counts, outcome, getattr(counts, outcome) + 1)
                    if outcome == "inserted" and self._settings.embeddings_enabled:
                        counts.embedding_pending += 1
                    if item.supersedes_id:
                        counts.superseded += 1
                    counts.possible_conflicts += len(conflicts)
                    conflict_ids.extend(conflicts)
                    global_index += 1

            result = CommitResult(
                batch_id=batch.id,
                counts=counts,
                assertion_ids=assertion_ids,
                conflict_ids=conflict_ids,
                rejected_items=rejected_items,
            )
            batch.state = BatchState.COMMITTED
            batch.committed_at = datetime.now(UTC)
            batch.result = result.model_dump(mode="json")
            # Once committed, retain only hashes/counters/result metadata, not
            # assertion staging text.
            await session.execute(delete(FlushPartRow).where(FlushPartRow.batch_id == batch.id))

        BATCH_TRANSITIONS.labels(state=BatchState.COMMITTED).inc()
        for outcome, value in counts.model_dump().items():
            if value:
                ASSERTION_OUTCOMES.labels(outcome=outcome).inc(value)
        return result

    async def _apply_item(
        self, session: AsyncSession, item: AssertionInput
    ) -> tuple[UUID, str, list[UUID]]:
        existing = await session.scalar(
            select(AssertionRow)
            .options(selectinload(AssertionRow.sources))
            .where(AssertionRow.content_hash == item.stable_hash)
            .with_for_update()
        )
        if existing:
            existing.confirmation_count += 1
            existing.last_confirmed_at = datetime.now(UTC)
            changed = self._enrich(existing, item)
            return existing.id, "enriched_updated" if changed else "confirmed_existing", []

        if item.supersedes_id:
            old = await session.get(AssertionRow, item.supersedes_id, with_for_update=True)
            if not old:
                raise NotFoundError(f"superseded assertion {item.supersedes_id} was not found")
            if old.status == AssertionStatus.SUPERSEDED:
                raise ConflictError(f"assertion {old.id} is already superseded")
            old.status = AssertionStatus.SUPERSEDED

        row = AssertionRow(
            content=item.content,
            normalized_content=item.normalized_content,
            content_hash=item.stable_hash,
            kind=item.kind,
            origin=item.origin,
            status=item.status,
            confidence=item.confidence,
            topics=item.topics,
            valid_from=item.valid_from,
            valid_to=item.valid_to,
            observed_at=item.observed_at,
            sensitivity=item.sensitivity,
            supersedes_id=item.supersedes_id,
            embedding_state=(
                EmbeddingState.PENDING
                if self._settings.embeddings_enabled
                else EmbeddingState.DISABLED
            ),
            sources=[
                SourceRow(
                    url=str(source.url),
                    title=source.title,
                    publisher=source.publisher,
                    retrieved_at=source.retrieved_at,
                )
                for source in item.sources
            ],
        )
        session.add(row)
        await session.flush()
        if self._settings.embeddings_enabled:
            session.add(EmbeddingJobRow(assertion_id=row.id, model=self._settings.embedding_model))
        conflicts = await self._record_conflicts(session, row)
        return row.id, "inserted", conflicts

    @staticmethod
    def _enrich(existing: AssertionRow, item: AssertionInput) -> bool:
        changed = False
        merged_topics = sorted(set(existing.topics) | set(item.topics))
        if merged_topics != sorted(existing.topics):
            existing.topics = merged_topics
            changed = True
        if item.confidence > existing.confidence:
            existing.confidence = item.confidence
            changed = True
        source_urls = {source.url for source in existing.sources}
        for source in item.sources:
            if str(source.url) not in source_urls:
                existing.sources.append(
                    SourceRow(
                        url=str(source.url),
                        title=source.title,
                        publisher=source.publisher,
                        retrieved_at=source.retrieved_at,
                    )
                )
                changed = True
        return changed

    @staticmethod
    async def _record_conflicts(session: AsyncSession, new: AssertionRow) -> list[UUID]:
        if new.supersedes_id:
            return []
        candidates = (
            await session.scalars(
                select(AssertionRow).where(
                    AssertionRow.id != new.id,
                    AssertionRow.kind == new.kind,
                    AssertionRow.status == AssertionStatus.CURRENT,
                    AssertionRow.topics.overlap(new.topics),
                )
            )
        ).all()
        conflict_ids: list[UUID] = []
        for candidate in candidates:
            if looks_contradictory(candidate.normalized_content, new.normalized_content):
                left, right = sorted((candidate.id, new.id), key=str)
                conflict = ConflictRow(
                    left_assertion_id=left,
                    right_assertion_id=right,
                    reason="opposite_polarity",
                )
                session.add(conflict)
                await session.flush()
                conflict_ids.append(conflict.id)
        return conflict_ids

    async def abort(self, principal_id: str, batch_id: UUID) -> bool:
        async with self._sessions.begin() as session:
            batch = await session.scalar(
                select(FlushBatchRow)
                .where(
                    FlushBatchRow.id == batch_id,
                    FlushBatchRow.principal_id == principal_id,
                )
                .with_for_update()
            )
            if not batch:
                raise NotFoundError("flush batch was not found")
            if batch.state == BatchState.ABORTED:
                return True
            if batch.state != BatchState.OPEN:
                raise ConflictError(f"cannot abort batch in {batch.state} state")
            batch.state = BatchState.ABORTED
            await session.execute(delete(FlushPartRow).where(FlushPartRow.batch_id == batch.id))
        BATCH_TRANSITIONS.labels(state=BatchState.ABORTED).inc()
        return True

    async def expire_and_purge(self) -> tuple[int, int]:
        now = datetime.now(UTC)
        retention_cutoff = now - timedelta(seconds=self._settings.staging_retention_seconds)
        async with self._sessions.begin() as session:
            expired = await session.execute(
                update(FlushBatchRow)
                .where(FlushBatchRow.state == BatchState.OPEN, FlushBatchRow.expires_at <= now)
                .values(state=BatchState.EXPIRED)
            )
            await session.execute(
                delete(FlushPartRow).where(
                    FlushPartRow.batch_id.in_(
                        select(FlushBatchRow.id).where(FlushBatchRow.state != BatchState.OPEN)
                    )
                )
            )
            purged = await session.execute(
                delete(FlushBatchRow).where(
                    FlushBatchRow.state.in_(
                        [BatchState.ABORTED, BatchState.EXPIRED, BatchState.COMMITTED]
                    ),
                    FlushBatchRow.updated_at < retention_cutoff,
                )
            )
        return (
            int(cast(CursorResult[Any], expired).rowcount or 0),
            int(cast(CursorResult[Any], purged).rowcount or 0),
        )
