import hashlib
import socket
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, func, literal, select, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
    """Process embedding jobs for the configured model only."""

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

    async def reclaim_stale(self) -> int:
        """Return claims whose lease expired (for example after a killed worker) to the queue."""
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=self._settings.embedding_claim_timeout_seconds)
        reclaimed = 0
        async with self._sessions.begin() as session:
            jobs = (
                await session.scalars(
                    select(EmbeddingJobRow)
                    .where(
                        EmbeddingJobRow.state == JobState.CLAIMED,
                        EmbeddingJobRow.claimed_at <= cutoff,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for job in jobs:
                job.claimed_by = None
                job.last_error_code = "claim_expired"
                await self._retry_or_bury(session, job, now)
                reclaimed += 1
        if reclaimed:
            EMBEDDING_OUTCOMES.labels(outcome="reclaimed").inc(reclaimed)
        return reclaimed

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
                            # A worker only embeds with the model it has loaded.
                            EmbeddingJobRow.model == self._settings.embedding_model,
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
        await self.reclaim_stale()
        claimed = await self.claim()
        if not claimed:
            await self.refresh_metrics()
            return 0
        vectors = await self._embed([content for _, content in claimed])
        if vectors is None and len(claimed) > 1:
            # One unembeddable text must not sink its neighbours: retry each item alone.
            for job, content in claimed:
                single = await self._embed([content])
                if single is None:
                    await self._fail(job.id, "provider_unavailable")
                else:
                    await self._complete(job.id, single[0])
        elif vectors is None:
            await self._fail(claimed[0][0].id, "provider_unavailable")
        else:
            for (job, _), vector in zip(claimed, vectors, strict=True):
                await self._complete(job.id, vector)
        await self.refresh_metrics()
        return len(claimed)

    async def _embed(self, texts: list[str]) -> list[list[float]] | None:
        try:
            vectors = await self._provider.embed(texts)
        except Exception:
            return None
        return vectors if len(vectors) == len(texts) else None

    async def _complete(self, job_id: UUID, vector: list[float]) -> None:
        async with self._sessions.begin() as session:
            job = await session.get(EmbeddingJobRow, job_id, with_for_update=True)
            if not job or job.state != JobState.CLAIMED:
                return
            assertion = await session.get(AssertionRow, job.assertion_id, with_for_update=True)
            if assertion:
                # The previous vector stays in place until this statement replaces it.
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
            outcome = await self._retry_or_bury(session, job, datetime.now(UTC))
        EMBEDDING_OUTCOMES.labels(outcome=outcome).inc()

    async def _retry_or_bury(
        self, session: AsyncSession, job: EmbeddingJobRow, now: datetime
    ) -> str:
        if job.attempts >= self._settings.embedding_max_attempts:
            job.state = JobState.DEAD
            assertion = await session.get(AssertionRow, job.assertion_id, with_for_update=True)
            # A failed rebuild must not hide a vector that is still valid for search.
            if assertion and assertion.embedding_model != job.model:
                assertion.embedding_state = EmbeddingState.FAILED
            return "dead"
        job.state = JobState.RETRY
        job.available_at = now + timedelta(seconds=retry_delay(job.attempts, job.id))
        return "retry"

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
        """Queue every assertion for `model`; rerunning it requeues instead of failing."""
        statement = pg_insert(EmbeddingJobRow).from_select(
            ["id", "assertion_id", "model", "replace_existing", "state", "attempts"],
            select(
                func.gen_random_uuid(),
                AssertionRow.id,
                literal(model),
                true(),
                literal(JobState.PENDING.value),
                literal(0),
            ),
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_embedding_job_assertion_model",
            set_={
                "state": JobState.PENDING.value,
                "attempts": 0,
                "replace_existing": True,
                "available_at": func.now(),
                "claimed_at": None,
                "claimed_by": None,
                "last_error_code": None,
            },
            # Leave a job alone while a live worker holds it.
            where=EmbeddingJobRow.state != JobState.CLAIMED,
        )
        async with self._sessions.begin() as session:
            queued = (await session.scalars(statement.returning(EmbeddingJobRow.id))).all()
        return len(queued)

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
