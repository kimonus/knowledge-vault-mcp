from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import Connection, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, SessionTransaction


class _InspectionSession(Session):
    """Every transaction, including implicit service transactions, is read-only."""


@event.listens_for(_InspectionSession, "after_begin")
def _read_only(session: Session, transaction: SessionTransaction, connection: Connection) -> None:
    del session, transaction
    connection.exec_driver_sql("SET TRANSACTION READ ONLY")


def async_database_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


class Database:
    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.engine: AsyncEngine = create_async_engine(
            async_database_url(url),
            pool_pre_ping=True,
            echo=echo,
            # Statement parameters carry assertion text; keep them out of exception messages.
            hide_parameters=True,
        )
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.read_sessions = async_sessionmaker(
            self.engine, expire_on_commit=False, sync_session_class=_InspectionSession
        )

    @property
    def sessions(self) -> async_sessionmaker[AsyncSession]:
        return self._sessions

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession]:
        async with self._sessions() as session:
            yield session

    async def ready(self) -> bool:
        from sqlalchemy import text

        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def close(self) -> None:
        await self.engine.dispose()
