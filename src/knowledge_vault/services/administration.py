import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from knowledge_vault.domain.models import AssertionInput, CommitResult
from knowledge_vault.persistence.tables import (
    AssertionRow,
    ConfirmationTokenRow,
    ConflictRow,
    DeletionAuditRow,
    EmbeddingJobRow,
)
from knowledge_vault.services.errors import ConflictError, NotFoundError
from knowledge_vault.services.ingestion import IngestionService


class AdministrationService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        ingestion: IngestionService,
    ) -> None:
        self._sessions = sessions
        self._ingestion = ingestion

    async def correct(
        self,
        principal_id: str,
        idempotency_key: str,
        correction: AssertionInput,
    ) -> CommitResult:
        if not correction.supersedes_id:
            raise ConflictError("a correction must identify supersedes_id")
        batch = await self._ingestion.begin(principal_id, idempotency_key, 1, 1)
        await self._ingestion.append(
            principal_id, batch.id, 1, [correction.model_dump(mode="json")]
        )
        return await self._ingestion.commit(principal_id, batch.id)

    async def list_conflicts(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        async with self._sessions() as session:
            rows = list(
                (
                    await session.scalars(
                        select(ConflictRow)
                        .where(ConflictRow.resolved_at.is_(None))
                        .order_by(ConflictRow.created_at, ConflictRow.id)
                        .limit(limit)
                    )
                ).all()
            )
        return [
            {
                "id": str(row.id),
                "left_assertion_id": str(row.left_assertion_id),
                "right_assertion_id": str(row.right_assertion_id),
                "reason": row.reason,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]

    async def preview_forgetting(
        self, principal_id: str, assertion_ids: list[UUID]
    ) -> dict[str, Any]:
        unique_ids = list(dict.fromkeys(assertion_ids))
        if not unique_ids or len(unique_ids) > 100:
            raise ValueError("provide a bounded list of 1 through 100 assertion IDs")
        async with self._sessions.begin() as session:
            found = list(
                (
                    await session.scalars(
                        select(AssertionRow.id).where(AssertionRow.id.in_(unique_ids))
                    )
                ).all()
            )
            token = secrets.token_urlsafe(32)
            session.add(
                ConfirmationTokenRow(
                    token_hash=hashlib.sha256(token.encode()).digest(),
                    principal_id=principal_id,
                    assertion_ids=found,
                    expires_at=datetime.now(UTC) + timedelta(minutes=10),
                )
            )
        return {
            "matched_ids": [str(item) for item in found],
            "matched_count": len(found),
            "confirmation_token": token,
            "expires_in_seconds": 600,
            "warning": "Confirmation permanently deletes assertion content and derived embeddings.",
        }

    async def confirm_forgetting(self, principal_id: str, token: str) -> dict[str, Any]:
        token_hash = hashlib.sha256(token.encode()).digest()
        async with self._sessions.begin() as session:
            confirmation = await session.get(ConfirmationTokenRow, token_hash, with_for_update=True)
            if not confirmation or confirmation.principal_id != principal_id:
                raise NotFoundError("confirmation token was not found")
            now = datetime.now(UTC)
            if confirmation.used_at or confirmation.expires_at <= now:
                raise ConflictError("confirmation token is expired or already used")
            ids = confirmation.assertion_ids
            # Preserve unrelated corrections but remove their pointer to a forgotten predecessor.
            await session.execute(
                update(AssertionRow)
                .where(AssertionRow.supersedes_id.in_(ids))
                .values(supersedes_id=None)
            )
            result = await session.execute(delete(AssertionRow).where(AssertionRow.id.in_(ids)))
            deleted_count = int(cast(CursorResult[Any], result).rowcount or 0)
            confirmation.used_at = now
            correlation_id = secrets.token_hex(16)
            session.add(
                DeletionAuditRow(
                    principal_id=principal_id,
                    deleted_count=deleted_count,
                    correlation_id=UUID(correlation_id),
                )
            )
        return {"deleted_count": deleted_count, "correlation_id": correlation_id}

    async def statistics(self) -> dict[str, Any]:
        async with self._sessions() as session:
            total = await session.scalar(select(func.count()).select_from(AssertionRow))
            status_rows = (
                await session.execute(
                    select(AssertionRow.status, func.count()).group_by(AssertionRow.status)
                )
            ).all()
            kind_rows = (
                await session.execute(
                    select(AssertionRow.kind, func.count()).group_by(AssertionRow.kind)
                )
            ).all()
            job_rows = (
                await session.execute(
                    select(EmbeddingJobRow.state, func.count()).group_by(EmbeddingJobRow.state)
                )
            ).all()
            conflicts = await session.scalar(
                select(func.count())
                .select_from(ConflictRow)
                .where(ConflictRow.resolved_at.is_(None))
            )
        return {
            "assertions_total": total or 0,
            "by_status": {str(key): count for key, count in status_rows},
            "by_kind": {str(key): count for key, count in kind_rows},
            "embedding_jobs": {str(key): count for key, count in job_rows},
            "unresolved_conflicts": conflicts or 0,
        }
