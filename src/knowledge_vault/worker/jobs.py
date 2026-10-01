import hashlib
import socket
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from knowledge_vault.config import Settings
from knowledge_vault.domain.enums import EmbeddingState, JobState
from knowledge_vault.embeddings.base import EmbeddingProvider
from knowledge_vault.observability.metrics import EMBEDDING_OUTCOMES, EMBEDDING_QUEUE
from knowledge_vault.persistence.tables import AssertionRow, EmbeddingJobRow


def retry_delay(
    attempt: int, job_id: UUID, *, base_seconds: int = 5, cap_seconds: int = 3600
) -> int:
    exponential = min(cap_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    jitter_source = hashlib.sha256(f"{job_id}:{attempt}".encode()).digest()[0]
    jitter = int(exponential * 0.2 * (jitter_source / 255))
    return exponential + jitter


class EmbeddingWorker:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        provider: EmbeddingProvider,
        worker_id: str | None = None,
    ) -> None:
        self._sessions = sessions
        self._settings = settings
        self._provider = provider
        self._worker_id = worker_id or socket.gethostname()

    async def claim(self) -> list[tuple[EmbeddingJobRow, str]]:
        now = datetime.now(UTC)
        async with self._sessions.begin() as session:
            jobs = list(
                (
                    await session.scalars(
                        select(EmbeddingJobRow)
                        .where(
                            EmbeddingJobRow.state.in_([JobState.PENDING, JobState.RETRY]),
                            EmbeddingJobRow.available_at <= now,
                        )
                        .order_by(EmbeddingJobRow.available_at, EmbeddingJobRow.id)
                        .limit(self._settings.embedding_batch_size)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            claimed: list[tuple[EmbeddingJobRow, str]] = []
            for job in jobs:
                assertion = await session.get(AssertionRow, job.assertion_id)
                if not assertion:
                    job.state = JobState.COMPLETED
                    continue
                job.state = JobState.CLAIMED
                job.claimed_at = now
                job.claimed_by = self._worker_id
                job.attempts += 1
                claimed.append((job, assertion.content))
            return claimed

    async def process_once(self) -> int:
        claimed = await self.claim()
        if not claimed:
            await self.refresh_metrics()
            return 0
        try:
            vectors = await self._provider.embed([content for _, content in claimed])
        except Exception:
            for job, _ in claimed:
                await self._fail(job.id, "provider_unavailable")
            return len(claimed)
        if len(vectors) != len(claimed):
            for job, _ in claimed:
                await self._fail(job.id, "invalid_batch_size")
            return len(claimed)
        for (job, _), vector in zip(claimed, vectors, strict=True):
            await self._complete(job.id, vector)
        await self.refresh_metrics()
        return len(claimed)

    async def _complete(self, job_id: UUID, vector: list[float]) -> None:
        async with self._sessions.begin() as session:
            job = await session.get(EmbeddingJobRow, job_id, with_for_update=True)
            if not job or job.state != JobState.CLAIMED:
                return
            assertion = await session.get(AssertionRow, job.assertion_id, with_for_update=True)
            if assertion:
                if job.replace_existing:
                    assertion.replacement_embedding = vector
                    assertion.replacement_embedding_model = job.model
                    assertion.embedding = assertion.replacement_embedding
                    assertion.embedding_model = assertion.replacement_embedding_model
                    assertion.replacement_embedding = None
                    assertion.replacement_embedding_model = None
                else:
                    assertion.embedding = vector
                    assertion.embedding_model = job.model
                assertion.embedding_state = EmbeddingState.READY
            job.state = JobState.COMPLETED
            job.claimed_by = None
        EMBEDDING_OUTCOMES.labels(outcome="completed").inc()

    async def _fail(self, job_id: UUID, error_code: str) -> None:
        async with self._sessions.begin() as session:
            job = await session.get(EmbeddingJobRow, job_id, with_for_update=True)
            if not job or job.state != JobState.CLAIMED:
                return
            job.last_error_code = error_code
            job.claimed_by = None
            if job.attempts >= self._settings.embedding_max_attempts:
                job.state = JobState.DEAD
                assertion = await session.get(AssertionRow, job.assertion_id, with_for_update=True)
                if assertion:
                    assertion.embedding_state = EmbeddingState.FAILED
                outcome = "dead"
            else:
                job.state = JobState.RETRY
                job.available_at = datetime.now(UTC) + timedelta(
                    seconds=retry_delay(job.attempts, job.id)
                )
                outcome = "retry"
        EMBEDDING_OUTCOMES.labels(outcome=outcome).inc()

    async def release_claims(self) -> int:
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(EmbeddingJobRow)
                .where(
                    EmbeddingJobRow.state == JobState.CLAIMED,
                    EmbeddingJobRow.claimed_by == self._worker_id,
                )
                .values(
                    state=JobState.RETRY,
                    claimed_by=None,
                    available_at=datetime.now(UTC),
                )
            )
        return int(cast(CursorResult[Any], result).rowcount or 0)

    async def enqueue_rebuild(self, model: str) -> int:
        async with self._sessions.begin() as session:
            assertion_ids = list((await session.scalars(select(AssertionRow.id))).all())
            for assertion_id in assertion_ids:
                session.add(
                    EmbeddingJobRow(
                        assertion_id=assertion_id,
                        model=model,
                        replace_existing=True,
                    )
                )
        return len(assertion_ids)

    async def refresh_metrics(self) -> None:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(EmbeddingJobRow.state, func.count()).group_by(EmbeddingJobRow.state)
                )
            ).all()
        for state in JobState:
            EMBEDDING_QUEUE.labels(state=state).set(0)
        for state, count in rows:
            EMBEDDING_QUEUE.labels(state=state).set(count)
