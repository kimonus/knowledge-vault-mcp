from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from knowledge_vault.domain.enums import AssertionStatus, EmbeddingState
from knowledge_vault.domain.models import AssertionInput, SearchFilters
from knowledge_vault.embeddings.providers import DeterministicFakeProvider
from knowledge_vault.persistence.tables import AssertionRow, EmbeddingJobRow, FlushPartRow
from knowledge_vault.services.cursors import InvalidCursorError
from knowledge_vault.services.errors import BatchIncompleteError, ConflictError, NotFoundError
from knowledge_vault.services.ingestion import IngestionService
from knowledge_vault.services.search import SearchService
from knowledge_vault.worker.jobs import EmbeddingWorker


def item(content: str, **overrides: object) -> AssertionInput:
    raw: dict[str, object] = {
        "content": content,
        "kind": "user_fact",
        "origin": "user",
        "topics": ["profile"],
    }
    raw.update(overrides)
    return AssertionInput.model_validate(raw)


@pytest.mark.integration
async def test_migration_batch_idempotency_dedup_search_worker_and_forgetting(
    integration_container,
) -> None:
    container = integration_container
    ingestion = container.ingestion
    principal = "test-user"

    batch = await ingestion.begin(principal, "flush-1", 2, 3)
    replay = await ingestion.begin(principal, "flush-1", 2, 3)
    assert replay.id == batch.id
    with pytest.raises(ConflictError, match="different totals"):
        await ingestion.begin(principal, "flush-1", 1, 1)

    first = [
        item("The user lives in Warsaw."),
        item("The user prefers Polish search results.", kind="preference"),
    ]
    accepted, rejected, replayed = await ingestion.append(
        principal,
        batch.id,
        1,
        [entry.model_dump(mode="json") for entry in first],
    )
    assert (accepted, rejected, replayed) == (2, [], False)
    assert (
        await ingestion.append(
            principal,
            batch.id,
            1,
            [entry.model_dump(mode="json") for entry in first],
        )
    )[2] is True
    with pytest.raises(ConflictError, match="different payload"):
        await ingestion.append(principal, batch.id, 1, [item("Different").model_dump(mode="json")])
    with pytest.raises(BatchIncompleteError):
        await ingestion.commit(principal, batch.id)

    secret_item = {
        "content": "password=correct-horse-battery-staple",
        "kind": "configuration",
        "origin": "user",
    }
    await ingestion.append(principal, batch.id, 2, [secret_item])
    result = await ingestion.commit(principal, batch.id)
    assert result.counts.inserted == 2
    assert result.counts.rejected == 1
    assert result.counts.embedding_pending == 2
    assert (await ingestion.commit(principal, batch.id)) == result
    async with container.database.sessions() as session:
        assert await session.scalar(select(FlushPartRow)) is None

    duplicate = await ingestion.begin(principal, "flush-duplicate", 1, 1)
    await ingestion.append(principal, duplicate.id, 1, [first[0].model_dump(mode="json")])
    duplicate_result = await ingestion.commit(principal, duplicate.id)
    assert duplicate_result.counts.confirmed_existing == 1
    existing = await container.search.get(result.assertion_ids[0])
    assert existing.confirmation_count == 2

    pending_page = await container.search.search("Warsaw", limit=10)
    assert pending_page.embedding_degraded is False
    assert [hit.assertion.content for hit in pending_page.results] == ["The user lives in Warsaw."]

    worker = EmbeddingWorker(
        container.database.sessions,
        container.settings,
        DeterministicFakeProvider(dimensions=384),
        worker_id="test-worker",
    )
    assert await worker.process_once() == 2
    async with container.database.sessions() as session:
        rows = list((await session.scalars(select(AssertionRow))).all())
        assert all(row.embedding_state == EmbeddingState.READY for row in rows)
        assert all(row.embedding is not None for row in rows)
        assert not list(
            (
                await session.scalars(
                    select(EmbeddingJobRow).where(EmbeddingJobRow.state != "completed")
                )
            ).all()
        )

    corrected = item(
        "The user no longer lives in Warsaw.",
        supersedes_id=result.assertion_ids[0],
    )
    correction = await container.administration.correct(principal, "correction-1", corrected)
    old = await container.search.get(result.assertion_ids[0])
    assert old.status is AssertionStatus.SUPERSEDED
    current_page = await container.search.search("Warsaw", SearchFilters(), limit=10)
    current_ids = [hit.assertion.id for hit in current_page.results]
    assert correction.assertion_ids[0] in current_ids
    assert result.assertion_ids[0] not in current_ids

    preview = await container.administration.preview_forgetting(principal, correction.assertion_ids)
    deleted = await container.administration.confirm_forgetting(
        principal, preview["confirmation_token"]
    )
    assert deleted["deleted_count"] == 1
    with pytest.raises(ConflictError, match="already used"):
        await container.administration.confirm_forgetting(principal, preview["confirmation_token"])
    with pytest.raises(NotFoundError):
        await container.search.get(correction.assertion_ids[0])

    statistics = await container.administration.statistics()
    assert statistics["assertions_total"] == 2


@pytest.mark.integration
async def test_abort_and_explicit_conflict(integration_container) -> None:
    container = integration_container
    principal = "test-user"
    batch = await container.ingestion.begin(principal, "abort", 1, 1)
    await container.ingestion.append(
        principal, batch.id, 1, [item("Temporary assertion").model_dump(mode="json")]
    )
    assert await container.ingestion.abort(principal, batch.id)
    assert await container.ingestion.abort(principal, batch.id)

    facts = await container.ingestion.begin(principal, "conflict", 1, 2)
    values = [item("user likes tea"), item("user does not likes tea")]
    await container.ingestion.append(
        principal, facts.id, 1, [value.model_dump(mode="json") for value in values]
    )
    result = await container.ingestion.commit(principal, facts.id)
    assert result.counts.possible_conflicts == 1
    assert len(await container.administration.list_conflicts()) == 1


@pytest.mark.integration
async def test_worker_retry_dead_rebuild_and_release(integration_container) -> None:
    container = integration_container
    batch = await container.ingestion.begin("test-user", "worker-errors", 1, 1)
    await container.ingestion.append(
        "test-user",
        batch.id,
        1,
        [item("The user reads Polish science fiction.").model_dump(mode="json")],
    )
    result = await container.ingestion.commit("test-user", batch.id)

    class FailingProvider:
        model_id = "failing"
        dimensions = 384

        async def embed(self, texts: list[str]) -> list[list[float]]:
            del texts
            raise RuntimeError("synthetic provider outage")

    retry_settings = container.settings.model_copy(update={"embedding_max_attempts": 2})
    failing = EmbeddingWorker(
        container.database.sessions,
        retry_settings,
        FailingProvider(),
        worker_id="failure-worker",
    )
    assert await failing.process_once() == 1
    async with container.database.sessions.begin() as session:
        job = await session.scalar(select(EmbeddingJobRow))
        assert job is not None
        assert job.state == "retry"
        job.available_at = datetime.now(UTC)
    assert await failing.process_once() == 1
    async with container.database.sessions() as session:
        job = await session.scalar(select(EmbeddingJobRow))
        assertion = await session.get(AssertionRow, result.assertion_ids[0])
        assert job is not None and job.state == "dead"
        assert assertion is not None and assertion.embedding_state == EmbeddingState.FAILED

    rebuild = EmbeddingWorker(
        container.database.sessions,
        retry_settings,
        DeterministicFakeProvider(dimensions=384, model_id="replacement"),
        worker_id="rebuild-worker",
    )
    assert await rebuild.enqueue_rebuild("replacement") == 1
    assert await rebuild.process_once() == 1
    async with container.database.sessions() as session:
        assertion = await session.get(AssertionRow, result.assertion_ids[0])
        assert assertion is not None
        assert assertion.embedding_model == "replacement"
        assert assertion.replacement_embedding is None

    assert await rebuild.enqueue_rebuild("replacement-2") == 1
    assert len(await rebuild.claim()) == 1
    assert await rebuild.release_claims() == 1
    async with container.database.sessions.begin() as session:
        await session.execute(
            update(EmbeddingJobRow)
            .where(EmbeddingJobRow.state == "retry")
            .values(available_at=datetime.now(UTC))
        )
    assert await rebuild.process_once() == 1
    assert await rebuild.process_once() == 0


@pytest.mark.integration
async def test_boundary_validation_enrichment_filters_and_pagination(integration_container) -> None:
    container = integration_container
    ingestion = container.ingestion
    principal = "test-user"

    for key, parts, items in [("", 1, 1), ("parts", 0, 1), ("items", 1, 0)]:
        with pytest.raises(ConflictError):
            await ingestion.begin(principal, key, parts, items)
    with pytest.raises(ConflictError, match="part must contain"):
        await ingestion.append(principal, uuid4(), 1, [])
    with pytest.raises(NotFoundError):
        await ingestion.append(principal, uuid4(), 1, [item("unknown").model_dump(mode="json")])
    with pytest.raises(NotFoundError):
        await ingestion.commit(principal, uuid4())
    with pytest.raises(NotFoundError):
        await ingestion.abort(principal, uuid4())

    ranged = await ingestion.begin(principal, "part-range", 1, 1)
    with pytest.raises(ConflictError, match="outside"):
        await ingestion.append(
            principal, ranged.id, 2, [item("outside range").model_dump(mode="json")]
        )

    expired = await ingestion.begin(principal, "expired", 1, 1)
    async with container.database.sessions.begin() as session:
        row = await session.get(type(expired), expired.id)
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(ConflictError, match="expired"):
        await ingestion.append(principal, expired.id, 1, [item("too late").model_dump(mode="json")])

    original = item(
        "The user writes software.",
        confidence=0.2,
        topics=["work"],
        sources=[{"url": "https://source.example/one", "title": "One"}],
    )
    first = await ingestion.begin(principal, "enrich-original", 1, 1)
    await ingestion.append(principal, first.id, 1, [original.model_dump(mode="json")])
    first_result = await ingestion.commit(principal, first.id)
    with pytest.raises(ConflictError, match="cannot append"):
        await ingestion.append(principal, first.id, 1, [original.model_dump(mode="json")])
    with pytest.raises(ConflictError, match="cannot abort"):
        await ingestion.abort(principal, first.id)

    enriched = item(
        "The user writes software.",
        confidence=0.9,
        topics=["work", "python"],
        sources=[
            {"url": "https://source.example/one", "title": "One"},
            {"url": "https://source.example/two", "title": "Two"},
        ],
    )
    second = await ingestion.begin(principal, "enrich-again", 1, 1)
    await ingestion.append(principal, second.id, 1, [enriched.model_dump(mode="json")])
    enriched_result = await ingestion.commit(principal, second.id)
    assert enriched_result.counts.enriched_updated == 1
    view = await container.search.get(first_result.assertion_ids[0])
    assert view.confidence == 0.9
    assert set(view.topics) == {"work", "python"}

    invalid_correction = await ingestion.begin(principal, "missing-supersession", 1, 1)
    await ingestion.append(
        principal,
        invalid_correction.id,
        1,
        [item("Replacement for missing assertion", supersedes_id=uuid4()).model_dump(mode="json")],
    )
    with pytest.raises(NotFoundError, match="superseded assertion"):
        await ingestion.commit(principal, invalid_correction.id)

    correction = item("The user writes Rust.", supersedes_id=first_result.assertion_ids[0])
    corrected = await ingestion.begin(principal, "supersede-once", 1, 1)
    await ingestion.append(principal, corrected.id, 1, [correction.model_dump(mode="json")])
    await ingestion.commit(principal, corrected.id)
    second_correction = await ingestion.begin(principal, "supersede-twice", 1, 1)
    await ingestion.append(
        principal,
        second_correction.id,
        1,
        [
            item("The user writes Go.", supersedes_id=first_result.assertion_ids[0]).model_dump(
                mode="json"
            )
        ],
    )
    with pytest.raises(ConflictError, match="already superseded"):
        await ingestion.commit(principal, second_correction.id)

    no_vectors = IngestionService(
        container.database.sessions,
        container.settings.model_copy(update={"embeddings_enabled": False}),
    )
    text_only = await no_vectors.begin(principal, "text-only", 1, 2)
    await no_vectors.append(
        principal,
        text_only.id,
        1,
        [
            item("The user reads software books.", sensitivity="private").model_dump(mode="json"),
            item("The user reviews software tools.").model_dump(mode="json"),
        ],
    )
    await no_vectors.commit(principal, text_only.id)

    now = datetime.now(UTC)
    filters = SearchFilters(
        kinds=["user_fact"],
        origins=["user"],
        statuses=["current"],
        topics=["profile"],
        sensitivities=["private", "normal"],
        minimum_confidence=0.1,
        valid_at=now,
        created_after=now - timedelta(days=1),
        created_before=now + timedelta(days=1),
    )
    page = await container.search.search("software", filters, limit=1)
    assert len(page.results) == 1
    assert page.next_cursor is not None
    next_page = await container.search.search("software", filters, limit=1, cursor=page.next_cursor)
    assert next_page.results
    with pytest.raises(InvalidCursorError):
        await container.search.search("different", filters, limit=1, cursor=page.next_cursor)
    with pytest.raises(ValueError, match="query"):
        await container.search.search("   ")
    with pytest.raises(ValueError, match="limit"):
        await container.search.search("software", limit=0)

    class SearchFailureProvider:
        model_id = "failure"
        dimensions = 384

        async def embed(self, texts: list[str]) -> list[list[float]]:
            del texts
            raise RuntimeError("synthetic query embedding failure")

    degraded = SearchService(
        container.database.sessions, container.settings, SearchFailureProvider()
    )
    assert (await degraded.search("software", limit=5)).embedding_degraded is True
    without_provider = SearchService(container.database.sessions, container.settings, None)
    assert (await without_provider.search("software", limit=5)).embedding_degraded is True
