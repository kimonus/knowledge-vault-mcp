from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


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
