from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from knowledge_vault.persistence.tables import HeartbeatRow

WORKER = "worker"
BACKUP = "backup"
WATCHDOG = "watchdog"


@dataclass(frozen=True, slots=True)
class Heartbeat:
    age_seconds: float
    detail: str | None


class OperationsService:
    """Record and read when each background duty last succeeded."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def beat(self, name: str, detail: str | None = None) -> None:
        statement = pg_insert(HeartbeatRow).values(name=name, detail=detail)
        statement = statement.on_conflict_do_update(
            index_elements=[HeartbeatRow.name],
            set_={"succeeded_at": func.now(), "detail": detail},
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)

    async def heartbeats(self) -> dict[str, Heartbeat]:
        age = func.extract("epoch", func.now() - HeartbeatRow.succeeded_at)
        async with self._sessions() as session:
            rows = (
                await session.execute(select(HeartbeatRow.name, age, HeartbeatRow.detail))
            ).all()
        return {name: Heartbeat(float(seconds), detail) for name, seconds, detail in rows}
