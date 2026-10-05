"""Regression tests for the 2026-10-01 independent review (finding IDs in test names/docstrings)."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from knowledge_vault.domain.enums import AssertionStatus, EmbeddingState
from knowledge_vault.domain.models import AssertionInput, SearchFilters
from knowledge_vault.embeddings.providers import DeterministicFakeProvider
from knowledge_vault.persistence.tables import (
    AssertionRow,
    ConfirmationTokenRow,
    ConflictRow,
    DeletionAuditRow,
    EmbeddingJobRow,
    FlushBatchRow,
    FlushPartRow,
    SourceRow,
)
from knowledge_vault.services.errors import ConflictError, InvalidRequestError, NotFoundError
from knowledge_vault.worker.jobs import EmbeddingWorker
from knowledge_vault.worker.runner import run_worker_loop

P = "test-user"


def item(content: str, **overrides: object) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "content": content,
        "kind": "user_fact",
        "origin": "user",
        "topics": ["review"],
    }
    raw.update(overrides)
    return raw


async def flush(container, key: str, items: list[dict[str, Any]], principal: str = P):
    batch = await container.ingestion.begin(principal, key, 1, len(items))
    await container.ingestion.append(principal, batch.id, 1, items)
    return await container.ingestion.commit(principal, batch.id)


def worker_for(container, *, worker_id: str = "w", provider=None, **settings: object):
    return EmbeddingWorker(
        container.database.sessions,
        container.settings.model_copy(update=settings),
        provider or DeterministicFakeProvider(dimensions=384),
        worker_id=worker_id,
    )


async def count(container, table, *conditions) -> int:
    async with container.database.sessions() as session:
        return int(
            await session.scalar(select(func.count()).select_from(table).where(*conditions)) or 0
        )


async def warm_pool(container, size: int) -> None:
    """Open `size` connections at once, so concurrent calls all read before any of them writes."""

    async def hold() -> None:
        async with container.database.engine.connect() as connection:
            await connection.execute(text("SELECT pg_sleep(0.05)"))

    await asyncio.gather(*(hold() for _ in range(size)))


@pytest.mark.integration
async def test_concurrent_begin_and_commit_are_idempotent(integration_container) -> None:
    container = integration_container
    await warm_pool(container, 8)
    batches = await asyncio.gather(
        *[container.ingestion.begin(P, "concurrent-begin", 1, 2) for _ in range(8)]
    )
    assert len({batch.id for batch in batches}) == 1
    assert any(batch.replayed for batch in batches)
    assert await count(container, FlushBatchRow) == 1

    await warm_pool(container, 8)
    uploads = await asyncio.gather(
        *[
            container.artifacts.begin(P, "concurrent-upload", "a.txt", "text/plain", 1)
            for _ in range(8)
        ]
    )
    assert len({upload.id for upload in uploads}) == 1

    batch = batches[0]
    await container.ingestion.append(P, batch.id, 1, [item("same one"), item("same two")])
    results = await asyncio.gather(*[container.ingestion.commit(P, batch.id) for _ in range(8)])
    assert all(result == results[0] for result in results)
    assert await count(container, AssertionRow) == 2


@pytest.mark.integration
async def test_concurrent_first_insert_of_identical_content_converges(
    integration_container,
) -> None:
    """KV-024: racing commits of the same new text all succeed and leave exactly one row."""
    container = integration_container
    batches = []
    for index in range(6):
        batch = await container.ingestion.begin(P, f"race-{index}", 1, 1)
        await container.ingestion.append(P, batch.id, 1, [item("identical brand new content")])
        batches.append(batch)
    results = await asyncio.gather(*[container.ingestion.commit(P, b.id) for b in batches])
    assert len({result.assertion_ids[0] for result in results}) == 1
    assert sum(result.counts.inserted for result in results) == 1
    async with container.database.sessions() as session:
        row = await session.scalar(select(AssertionRow))
    assert row is not None and row.confirmation_count == 6


@pytest.mark.integration
async def test_correction_back_to_known_wording_supersedes_and_revives(
    integration_container,
) -> None:
    """KV-008: A -> B -> A must leave A current and B superseded, with truthful counts."""
    container = integration_container
    first = await flush(container, "a", [item("The user lives in Krakow.")])
    a_id = first.assertion_ids[0]
    second = await container.administration.correct(
        P,
        "b",
        AssertionInput.model_validate(item("The user lives in Gdansk.", supersedes_id=str(a_id))),
    )
    b_id = second.assertion_ids[0]
    back = await container.administration.correct(
        P,
        "back",
        AssertionInput.model_validate(item("The user lives in Krakow.", supersedes_id=str(b_id))),
    )
    assert back.assertion_ids == [a_id]
    assert back.counts.superseded == 1
    assert back.counts.enriched_updated == 1
    assert (await container.search.get(a_id)).status is AssertionStatus.CURRENT
    assert (await container.search.get(b_id)).status is AssertionStatus.SUPERSEDED
    page = await container.search.search("Krakow", limit=5)
    assert [hit.assertion.id for hit in page.results] == [a_id]

    self_reference = await container.ingestion.begin(P, "self", 1, 1)
    await container.ingestion.append(
        P, self_reference.id, 1, [item("The user lives in Krakow.", supersedes_id=str(a_id))]
    )
    with pytest.raises(ConflictError, match="cannot supersede itself"):
        await container.ingestion.commit(P, self_reference.id)


@pytest.mark.integration
async def test_reaffirmation_upgrades_uncertain_status_and_counts_no_supersession(
    integration_container,
) -> None:
    container = integration_container
    await flush(container, "u1", [item("The user might adopt a cat.", status="uncertain")])
    result = await flush(container, "u2", [item("The user might adopt a cat.", status="current")])
    assert result.counts.enriched_updated == 1
    assert result.counts.superseded == 0
    view = await container.search.get(result.assertion_ids[0])
    assert view.status is AssertionStatus.CURRENT


@pytest.mark.integration
async def test_sources_round_trip_through_retrieval(integration_container) -> None:
    """KV-012: provenance is returned by get and search."""
    container = integration_container
    result = await flush(
        container,
        "sources",
        [
            item(
                "PostgreSQL 17 was released in 2024.",
                kind="external_fact",
                origin="external_source",
                sources=[
                    {
                        "url": "https://www.postgresql.org/about/news/",
                        "title": "PostgreSQL news",
                        "publisher": "PGDG",
                        "retrieved_at": "2026-01-01T00:00:00",
                    }
                ],
            )
        ],
    )
    view = await container.search.get(result.assertion_ids[0])
    assert [source.url for source in view.sources] == ["https://www.postgresql.org/about/news/"]
    assert view.sources[0].publisher == "PGDG"
    assert view.sources[0].retrieved_at == datetime(2026, 1, 1, tzinfo=UTC)
    page = await container.search.search("PostgreSQL released", limit=5)
    assert page.results[0].assertion.sources[0].title == "PostgreSQL news"


@pytest.mark.integration
async def test_naive_timestamps_are_stored_as_utc_and_filters_accept_them(
    integration_container,
) -> None:
    """KV-005: mixing offset-less and offset-aware timestamps no longer raises."""
    container = integration_container
    result = await flush(
        container,
        "naive",
        [item("Mixed timestamps.", valid_from="2026-01-01T00:00:00", valid_to="2026-06-01T00:00Z")],
    )
    view = await container.search.get(result.assertion_ids[0])
    assert view.valid_from == datetime(2026, 1, 1, tzinfo=UTC)
    page = await container.search.search(
        "timestamps", SearchFilters(valid_at=datetime(2026, 2, 1)), limit=5
    )
    assert len(page.results) == 1
    with pytest.raises(InvalidRequestError, match="NUL"):
        await container.search.search("bad\x00query")


@pytest.mark.integration
async def test_nul_content_is_rejected_per_item_before_staging(integration_container) -> None:
    """KV-006: the input that used to raise a database error is now an item rejection."""
    container = integration_container
    batch = await container.ingestion.begin(P, "nul", 1, 2)
    accepted, rejected, _ = await container.ingestion.append(
        P, batch.id, 1, [item("fine"), item("before\x00after")]
    )
    assert accepted == 1
    assert [entry.index for entry in rejected] == [1]
    assert "NUL" in rejected[0].message


@pytest.mark.integration
async def test_expired_batches_cannot_commit_and_staging_is_purged(integration_container) -> None:
    """KV-009: expiry is enforced at commit and by the periodic maintenance pass."""
    container = integration_container
    past = datetime.now(UTC) - timedelta(days=30)

    late = await container.ingestion.begin(P, "late-commit", 1, 1)
    await container.ingestion.append(P, late.id, 1, [item("Staged then expired.")])
    abandoned = await container.ingestion.begin(P, "abandoned", 1, 1)
    await container.ingestion.append(P, abandoned.id, 1, [item("Abandoned staged text.")])
    async with container.database.sessions.begin() as session:
        await session.execute(update(FlushBatchRow).values(expires_at=past))

    with pytest.raises(ConflictError, match="expired"):
        await container.ingestion.commit(P, late.id)
    # The expiry is durable even though the call raised.
    async with container.database.sessions() as session:
        assert (
            await session.scalar(select(FlushBatchRow.state).where(FlushBatchRow.id == late.id))
            == "expired"
        )
    assert await count(container, FlushPartRow, FlushPartRow.batch_id == late.id) == 0
    assert await count(container, AssertionRow) == 0

    expired, purged = await container.ingestion.expire_and_purge()
    assert (expired, purged) == (1, 0)
    assert await count(container, FlushPartRow) == 0

    # Metadata older than the retention window is removed entirely. Plain SQL is used because
    # the ORM would refresh updated_at through its onupdate hook.
    async with container.database.sessions.begin() as session:
        await session.execute(
            text("UPDATE flush_batches SET updated_at = now() - interval '1 year'")
        )
    assert (await container.ingestion.expire_and_purge())[1] == 2
    assert await count(container, FlushBatchRow) == 0

    target = await flush(container, "token-target", [item("to forget")])
    await container.administration.preview_forgetting(P, target.assertion_ids)
    assert await container.administration.purge_confirmation_tokens() == 0
    async with container.database.sessions.begin() as session:
        await session.execute(update(ConfirmationTokenRow).values(expires_at=past))
    assert await container.administration.purge_confirmation_tokens() == 1


@pytest.mark.integration
async def test_worker_reclaims_claims_left_by_a_killed_worker(integration_container) -> None:
    """KV-010: a lease that outlives its timeout returns to the queue for another worker."""
    container = integration_container
    await flush(container, "lease", [item(f"lease item {index}") for index in range(3)])
    crashed = worker_for(container, worker_id="pod-old")
    assert len(await crashed.claim()) == 3

    replacement = worker_for(container, worker_id="pod-new")
    assert await replacement.process_once() == 0  # leases are still fresh
    async with container.database.sessions.begin() as session:
        await session.execute(
            update(EmbeddingJobRow).values(claimed_at=datetime.now(UTC) - timedelta(hours=1))
        )
    assert await replacement.reclaim_stale() == 3
    async with container.database.sessions.begin() as session:
        await session.execute(update(EmbeddingJobRow).values(available_at=datetime.now(UTC)))
    assert await replacement.process_once() == 3
    assert await count(container, AssertionRow, AssertionRow.embedding_state == "ready") == 3

    # A job that keeps losing its lease is buried once its attempts are exhausted.
    await flush(container, "lease-dead", [item("always crashes")])
    stubborn = worker_for(container, worker_id="pod-x", embedding_max_attempts=1)
    assert len(await stubborn.claim()) == 1
    async with container.database.sessions.begin() as session:
        await session.execute(
            update(EmbeddingJobRow)
            .where(EmbeddingJobRow.state == "claimed")
            .values(claimed_at=datetime.now(UTC) - timedelta(hours=1))
        )
    assert await stubborn.reclaim_stale() == 1
    assert await count(container, EmbeddingJobRow, EmbeddingJobRow.state == "dead") == 1


@pytest.mark.integration
async def test_one_unembeddable_item_does_not_fail_its_batch(integration_container) -> None:
    """KV-010: a provider failure for one text is isolated to that text."""
    container = integration_container
    await flush(container, "poison", [item(f"poison batch item {index}") for index in range(4)])

    class OnePoison:
        model_id = "fake"
        dimensions = 384

        async def embed(self, texts: list[str]) -> list[list[float]]:
            if any("item 2" in value for value in texts):
                raise RuntimeError("cannot embed")
            return await DeterministicFakeProvider(dimensions=384).embed(texts)

    worker = worker_for(container, provider=OnePoison(), embedding_max_attempts=1)
    assert await worker.process_once() == 4
    async with container.database.sessions() as session:
        states = dict(
            (
                await session.execute(
                    select(AssertionRow.embedding_state, func.count()).group_by(
                        AssertionRow.embedding_state
                    )
                )
            ).all()
        )
    assert states == {EmbeddingState.READY: 3, EmbeddingState.FAILED: 1}


@pytest.mark.integration
async def test_rebuild_is_repeatable_and_models_do_not_mix(integration_container) -> None:
    """KV-010: re-embedding can be rerun, and vectors of another model are ignored by search."""
    container = integration_container
    await flush(
        container, "rebuild", [item("rebuild gardening one"), item("rebuild gardening two")]
    )
    current = worker_for(container)
    assert await current.process_once() == 2
    model = container.settings.embedding_model

    # Rerunning for the current model requeues instead of violating the unique constraint.
    assert await current.enqueue_rebuild(model) == 2
    assert await current.enqueue_rebuild(model) == 2
    assert await current.process_once() == 2
    assert await count(container, AssertionRow, AssertionRow.embedding_state == "ready") == 2

    # Jobs for another model are left for a worker configured with that model.
    assert await current.enqueue_rebuild("model-b") == 2
    assert await current.process_once() == 0
    model_b = worker_for(
        container,
        provider=DeterministicFakeProvider(dimensions=384, model_id="model-b"),
        embedding_model="model-b",
    )
    assert await model_b.process_once() == 2
    assert await count(container, AssertionRow, AssertionRow.embedding_model == "model-b") == 2

    # The API still configured for the original model must not compare against model-b vectors.
    async with container.database.sessions() as session:
        statement_count = await session.scalar(
            select(func.count())
            .select_from(AssertionRow)
            .where(AssertionRow.embedding_model == model)
        )
    assert statement_count == 0
    page = await container.search.search("zzz-no-lexical-match", limit=5)
    assert page.results == []
    lexical = await container.search.search("gardening", limit=5)
    assert len(lexical.results) == 2

    # A failed rebuild keeps a still-valid vector searchable.
    class Failing:
        model_id = "model-b"
        dimensions = 384

        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("down")

    await model_b.enqueue_rebuild("model-b")
    failing = worker_for(
        container, provider=Failing(), embedding_model="model-b", embedding_max_attempts=1
    )
    assert await failing.process_once() == 2
    assert await count(container, EmbeddingJobRow, EmbeddingJobRow.state == "dead") == 2
    assert await count(container, AssertionRow, AssertionRow.embedding_state == "ready") == 2


@pytest.mark.integration
async def test_forgetting_cascades_and_reports_hidden_predecessors(integration_container) -> None:
    """Hard deletion removes dependent rows; audit and token rows stay content-free."""
    container = integration_container
    first = await flush(
        container, "tea", [item("user likes tea", sources=[{"url": "https://example.org/tea"}])]
    )
    negated = await flush(container, "no-tea", [item("user does not likes tea")])
    assert negated.counts.possible_conflicts == 1
    a_id = first.assertion_ids[0]
    successor = await container.administration.correct(
        P,
        "green",
        AssertionInput.model_validate(item("user likes green tea", supersedes_id=str(a_id))),
    )
    s_id = successor.assertion_ids[0]

    # KV-024: superseding one side resolves the conflict that involved it.
    assert await container.administration.list_conflicts() == []
    assert await count(container, ConflictRow, ConflictRow.resolved_at.is_not(None)) == 1

    preview = await container.administration.preview_forgetting(P, [s_id])
    assert preview["matched_ids"] == [str(s_id)]
    assert preview["superseded_predecessor_ids"] == [str(a_id)]

    other = await container.administration.preview_forgetting("someone-else", [s_id])
    with pytest.raises(NotFoundError):
        await container.administration.confirm_forgetting(P, other["confirmation_token"])

    outcomes = await asyncio.gather(
        *[
            container.administration.confirm_forgetting(P, preview["confirmation_token"])
            for _ in range(5)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(outcome, dict) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, ConflictError) for outcome in outcomes) == 4

    preview = await container.administration.preview_forgetting(P, [a_id])
    await container.administration.confirm_forgetting(P, preview["confirmation_token"])
    assert await count(container, SourceRow) == 0
    assert (
        await count(container, EmbeddingJobRow, EmbeddingJobRow.assertion_id.in_([a_id, s_id])) == 0
    )
    assert await count(container, ConflictRow) == 0
    async with container.database.sessions() as session:
        audit = (await session.scalars(select(DeletionAuditRow))).all()
        leaked = await session.scalar(
            text("SELECT count(*) FROM flush_batches WHERE result::text ILIKE '%tea%'")
        )
    assert {row.deleted_count for row in audit} == {1}
    assert set(DeletionAuditRow.__table__.columns.keys()) == {
        "id",
        "principal_id",
        "action",
        "deleted_count",
        "correlation_id",
        "created_at",
    }
    assert leaked == 0


@pytest.mark.integration
async def test_pagination_is_complete_and_duplicate_free_for_a_stable_corpus(
    integration_container,
) -> None:
    container = integration_container
    await flush(
        container,
        "pages",
        [item(f"pagination entry {index} about gardening") for index in range(37)],
    )
    await worker_for(container, embedding_batch_size=64).process_once()
    seen = []
    cursor = None
    for _ in range(20):
        page = await container.search.search("gardening", limit=5, cursor=cursor)
        seen.extend(hit.assertion.id for hit in page.results)
        cursor = page.next_cursor
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 37


@pytest.mark.integration
async def test_worker_loop_runs_maintenance_survives_errors_and_releases_claims(
    integration_container,
) -> None:
    """KV-009/KV-010: the loop the worker process runs, exercised without the process wrapper."""
    container = integration_container
    await flush(container, "loop", [item("loop item")])
    worker = worker_for(container)
    stopping = asyncio.Event()
    calls = {"maintenance": 0}

    async def maintenance() -> None:
        calls["maintenance"] += 1
        if calls["maintenance"] == 1:
            raise RuntimeError("transient database outage")
        await container.ingestion.expire_and_purge()
        await container.administration.purge_confirmation_tokens()
        stopping.set()

    await asyncio.wait_for(
        run_worker_loop(
            worker, maintenance, stopping, poll_seconds=0.01, maintenance_interval_seconds=0.0
        ),
        timeout=20,
    )
    assert calls["maintenance"] == 2
    assert await count(container, AssertionRow, AssertionRow.embedding_state == "ready") == 1
    assert await count(container, EmbeddingJobRow, EmbeddingJobRow.state == "claimed") == 0


@pytest.mark.integration
async def test_database_rejects_values_outside_the_enumerations(integration_container) -> None:
    """KV-013: revision 0002 enforces enumerated columns in PostgreSQL itself."""
    container = integration_container
    result = await flush(container, "enum", [item("enum check")])
    for statement in (
        "UPDATE assertions SET status = 'bogus'",
        "UPDATE assertions SET kind = 'bogus'",
        "UPDATE assertions SET sensitivity = 'bogus'",
        "UPDATE embedding_jobs SET state = 'bogus'",
        "UPDATE flush_batches SET state = 'bogus'",
    ):
        with pytest.raises(IntegrityError):
            async with container.database.sessions.begin() as session:
                await session.execute(text(statement))
    assert (await container.search.get(result.assertion_ids[0])).status is AssertionStatus.CURRENT


@pytest.mark.integration
def test_migrations_downgrade_and_upgrade_through_every_revision(postgres_url: str) -> None:
    """KV-013: each revision is self-contained DDL and the chain is reversible."""
    del postgres_url
    config = Config("alembic.ini")
    command.downgrade(config, "0001_initial")
    command.downgrade(config, "base")
    command.upgrade(config, "0001_initial")
    command.upgrade(config, "head")
    command.check(config)


@pytest.mark.integration
async def test_repeated_source_urls_commit_once_and_other_violations_are_not_retried(
    integration_container, monkeypatch
) -> None:
    """A deterministic constraint violation must not be retried or reported as a write race."""
    container = integration_container
    twice = [{"url": "https://example.org/a", "title": "first"}, {"url": "https://example.org/a"}]
    result = await flush(container, "dup-sources", [item("Has a repeated source.", sources=twice)])
    view = await container.search.get(result.assertion_ids[0])
    assert [(source.url, source.title) for source in view.sources] == [
        ("https://example.org/a", "first")
    ]

    more = [{"url": "https://example.org/b"}, {"url": "https://example.org/b"}]
    enriched = await flush(container, "dup-enrich", [item("Has a repeated source.", sources=more)])
    assert enriched.counts.enriched_updated == 1
    assert await count(container, SourceRow) == 2

    batch = await container.ingestion.begin(P, "not-a-race", 1, 1)
    await container.ingestion.append(P, batch.id, 1, [item("Never committed.")])
    calls = 0

    async def violates_another_constraint(principal_id, batch_id):
        nonlocal calls
        calls += 1
        raise IntegrityError("INSERT", {}, Exception("unrelated unique violation"))

    monkeypatch.setattr(container.ingestion, "_commit_once", violates_another_constraint)
    with pytest.raises(IntegrityError):
        await container.ingestion.commit(P, batch.id)
    assert calls == 1


@pytest.mark.integration
async def test_conflicts_are_detected_without_shared_topics(integration_container) -> None:
    """KV-024: contradiction detection does not depend on topics and stays bounded."""
    container = integration_container
    untagged = await flush(
        container,
        "no-topics",
        [item("user sleeps early", topics=[]), item("user never sleeps early", topics=[])],
    )
    assert untagged.counts.possible_conflicts == 1

    await flush(container, "topic-a", [item("user likes opera", topics=["music"])])
    other_topic = await flush(
        container, "topic-b", [item("user does not likes opera", topics=["evenings"])]
    )
    assert other_topic.counts.possible_conflicts == 1

    unrelated = await flush(
        container,
        "unrelated",
        [item("user likes jazz", topics=["music"]), item("user never smokes")],
    )
    assert unrelated.counts.possible_conflicts == 0
    assert len(await container.administration.list_conflicts()) == 2


@pytest.mark.integration
async def test_commit_reports_what_happened_to_each_item(integration_container) -> None:
    """A client can tell from the commit alone which items need no readback."""
    container = integration_container
    stored = await flush(
        container,
        "outcomes-seed",
        [
            item("The access point uses channel 36.", kind="configuration", confidence=0.8),
            item("The router sits in the hallway."),
            item("user likes coffee"),
        ],
    )
    channel_id, router_id, _ = stored.assertion_ids
    assert [entry.outcome for entry in stored.items] == ["inserted"] * 3
    assert stored.readback_ids == []
    assert all(not entry.ignored_fields for entry in stored.items)

    result = await flush(
        container,
        "outcomes-incremental",
        [
            # Same text apart from case and spacing, with metadata that the match does not apply.
            item(
                "the access point  uses channel 36.",
                kind="user_fact",
                confidence=0.5,
                sensitivity="private",
            ),
            {"content": "password=correct-horse-battery-staple", "kind": "user_fact"},
            item("The router sits in the hallway.", topics=["review", "network"]),
            item("The access point uses channel 44.", supersedes_id=str(channel_id)),
            item("user does not likes coffee"),
        ],
    )
    confirmed, enriched, replacement, contradiction = result.items
    assert [entry.index for entry in result.items] == [0, 2, 3, 4]
    assert [entry.index for entry in result.rejected_items] == [1]
    assert [entry.assertion_id for entry in result.items] == result.assertion_ids

    assert (confirmed.assertion_id, confirmed.outcome) == (channel_id, "confirmed_existing")
    assert confirmed.ignored_fields == ["content", "kind", "confidence", "sensitivity"]
    assert (enriched.assertion_id, enriched.outcome) == (router_id, "enriched_updated")
    assert enriched.ignored_fields == []
    assert (replacement.outcome, replacement.superseded_id) == ("inserted", channel_id)
    assert contradiction.outcome == "inserted"
    assert contradiction.conflict_ids == result.conflict_ids
    assert len(contradiction.conflict_ids) == 1
    assert all(not entry.conflict_ids for entry in (confirmed, enriched, replacement))
    # Only the record that kept stored values and the one that conflicts are worth reading;
    # the enriched record and the replacement are exactly what was submitted.
    assert result.readback_ids == [confirmed.assertion_id, contradiction.assertion_id]

    # The stored result replays unchanged and holds no assertion text.
    async with container.database.sessions() as session:
        batch = await session.scalar(
            select(FlushBatchRow).where(FlushBatchRow.id == result.batch_id)
        )
        assert batch is not None
        assert "channel" not in str(batch.result)
        legacy = {
            key: value
            for key, value in batch.result.items()
            if key not in ("items", "readback_ids")
        }
    assert await container.ingestion.commit(P, result.batch_id) == result

    # A result committed before per-item outcomes existed still replays.
    async with container.database.sessions.begin() as session:
        await session.execute(
            update(FlushBatchRow).where(FlushBatchRow.id == result.batch_id).values(result=legacy)
        )
    replayed = await container.ingestion.commit(P, result.batch_id)
    assert replayed.items == []
    assert replayed.readback_ids == []
    assert replayed.assertion_ids == result.assertion_ids
