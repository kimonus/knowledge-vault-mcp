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
        self._tokens: asyncio.Queue[None] = asyncio.Queue(maxsize=max_concurrent_checks)
        for _ in range(max_concurrent_checks):
            self._tokens.put_nowait(None)
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
        try:
            self._tokens.get_nowait()
        except asyncio.QueueEmpty:
            return False
        try:
            return await asyncio.wait_for(self._matches(), timeout=self._timeout)
        except Exception:
            # Never log database exceptions: their messages can contain credentials or data.
            return False
        finally:
            self._tokens.put_nowait(None)

    async def _matches(self) -> bool:
        async with self._sessions() as session:
            result = await session.execute(text("SELECT version_num FROM alembic_version LIMIT 2"))
            return result.scalars().all() == [self._expected]
