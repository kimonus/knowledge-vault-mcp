"""Heartbeats, operational statistics, and the watchdog."""

from typing import Any

import pytest
from sqlalchemy import text, update

from knowledge_vault.container import Container
from knowledge_vault.persistence.tables import EmbeddingJobRow
from knowledge_vault.services.operations import BACKUP, WATCHDOG, WORKER
from knowledge_vault.watchdog import evaluate, run

pytestmark = pytest.mark.integration


async def _clear(container: Container) -> None:
    async with container.database.sessions.begin() as session:
        await session.execute(text("TRUNCATE operational_heartbeats"))


async def _age(container: Container, name: str, interval: str) -> None:
    async with container.database.sessions.begin() as session:
        await session.execute(
            text(
                "UPDATE operational_heartbeats SET succeeded_at = now() - CAST(:age AS interval) "
                "WHERE name = :name"
            ),
            {"age": interval, "name": name},
        )


def _expecting_backup(container: Container) -> Container:
    container.settings = container.settings.model_copy(update={"watchdog_expect_backup": True})
    return container


async def test_heartbeats_are_upserted_and_reported_in_statistics(integration_container) -> None:
    container = integration_container
    await _clear(container)
    statistics = await container.administration.statistics()
    assert statistics["operations"] == {
        "worker_heartbeat_age_seconds": None,
        "backup_heartbeat_age_seconds": None,
    }

    await container.operations.beat(WORKER)
    await container.operations.beat(WORKER)
    await container.operations.beat(BACKUP)
    await _age(container, BACKUP, "2 hours")
    operations = (await container.administration.statistics())["operations"]
    assert 0 <= operations["worker_heartbeat_age_seconds"] < 60
    assert 7190 < operations["backup_heartbeat_age_seconds"] < 7260
    beats = await container.operations.heartbeats()
    assert set(beats) == {WORKER, BACKUP} and beats[WORKER].detail is None


async def test_watchdog_reports_stale_duties_and_dead_jobs(integration_container) -> None:
    container = _expecting_backup(integration_container)
    await _clear(container)
    assert [finding.code for finding in await evaluate(container)] == [
        "worker_stale",
        "backup_stale",
    ]

    await container.operations.beat(WORKER)
    await container.operations.beat(BACKUP)
    assert await evaluate(container) == []

    await _age(container, WORKER, "1 hour")
    await _age(container, BACKUP, "3 days")
    batch = await container.ingestion.begin("test-user", "watchdog-dead", 1, 1)
    await container.ingestion.append(
        "test-user", batch.id, 1, [{"content": "x", "kind": "user_fact", "origin": "user"}]
    )
    await container.ingestion.commit("test-user", batch.id)
    async with container.database.sessions.begin() as session:
        await session.execute(update(EmbeddingJobRow).values(state="dead"))
    findings = await evaluate(container)
    assert [finding.code for finding in findings] == [
        "worker_stale",
        "backup_stale",
        "embedding_jobs_dead",
    ]
    assert "1 embedding jobs are dead" in findings[-1].message

    # A deployment without the backup job is not told that backups are missing.
    container.settings = container.settings.model_copy(update={"watchdog_expect_backup": False})
    assert "backup_stale" not in [finding.code for finding in await evaluate(container)]


async def test_watchdog_announces_changes_once_and_reminds_later(integration_container) -> None:
    container = _expecting_backup(integration_container)
    await _clear(container)
    sent: list[tuple[str, str, list[str]]] = []

    async def notify(title: str, message: str, codes: list[str]) -> None:
        sent.append((title, message, codes))

    await container.operations.beat(WORKER)
    await container.operations.beat(BACKUP)
    assert await run(container, notify) == 0
    assert sent == []  # a healthy first run is not news

    await _age(container, BACKUP, "3 days")
    assert await run(container, notify) == 1
    assert await run(container, notify) == 1
    assert len(sent) == 1 and sent[0][2] == ["backup_stale"]
    assert sent[0][0] == "Knowledge Vault: 1 problem(s)"

    # The same problem is announced again only after the reminder interval.
    await _age(container, WATCHDOG, "2 days")
    assert await run(container, notify) == 1
    assert len(sent) == 2

    await container.operations.beat(BACKUP)
    assert await run(container, notify) == 0
    assert sent[-1][0] == "Knowledge Vault: recovered" and sent[-1][2] == []
    assert await run(container, notify) == 0
    assert len(sent) == 3

    # Without a webhook the state is still tracked and the exit code still reflects health.
    await _age(container, WORKER, "1 hour")
    assert await run(container, None) == 1
    assert (await container.operations.heartbeats())[WATCHDOG].detail == "worker_stale"


async def test_watchdog_retries_a_failed_notification(integration_container) -> None:
    container = integration_container
    await _clear(container)
    attempts: list[Any] = []

    async def failing(title: str, message: str, codes: list[str]) -> None:
        attempts.append(codes)
        raise ConnectionError("webhook unreachable")

    assert await run(container, failing) == 1  # worker heartbeat missing
    assert WATCHDOG not in await container.operations.heartbeats()
    assert await run(container, failing) == 1
    assert len(attempts) == 2  # not marked as delivered, so it is tried again
