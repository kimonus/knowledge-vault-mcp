"""Fail-closed schema compatibility checks for standalone processes."""

import asyncio

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class SchemaGuard:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        timeout_seconds: float = 2.0,
        max_concurrent_checks: int = 4,
    ) -> None:
        self._sessions = sessions
        self._timeout = timeout_seconds
        if max_concurrent_checks < 1:
            raise ValueError("max_concurrent_checks must be positive")
        # One bounded database check serves every overlapping caller. Never reuse a
        # completed positive result for a later request (no positive-result cache).
        self._inflight: asyncio.Task[bool] | None = None
        self._expected: str | None = None
        try:
            heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
            if len(heads) == 1 and heads[0]:
                self._expected = heads[0]
        except Exception:
            # Missing or invalid shipped migration assets must fail closed.
            self._expected = None

    async def ready(self) -> bool:
        if self._expected is None:
            return False
        task = self._inflight
        if task is None or task.done():
            task = asyncio.create_task(self._bounded_check())
            self._inflight = task
        # A disconnected/cancelled request must not cancel other callers' check.
        return await asyncio.shield(task)

    async def _bounded_check(self) -> bool:
        try:
            return await asyncio.wait_for(self._matches(), timeout=self._timeout)
        except Exception:
            # Never log database exceptions: their messages can contain credentials or data.
            return False

    async def _matches(self) -> bool:
        async with self._sessions() as session:
            result = await session.execute(text("SELECT version_num FROM alembic_version LIMIT 2"))
            return result.scalars().all() == [self._expected]
