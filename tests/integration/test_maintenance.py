"""Synthetic lifecycle, concurrency, compatibility and access-accounting regressions."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from knowledge_vault.domain.maintenance import (
    ContextInput,
    LinkInput,
    MetadataUpdate,
    TopicAliasInput,
)
from knowledge_vault.domain.models import SearchFilters
from knowledge_vault.persistence.tables import (
    AssertionLinkRow,
    AssertionRow,
    AssertionUsageRow,
    MaintenanceEventRow,
)
from knowledge_vault.services.errors import ConflictError, InvalidRequestError, NotFoundError

pytestmark = pytest.mark.integration
NOW = datetime.now(UTC)


async def seed(c, contents=None, **extra):
    contents = contents or ["The test router uses channel 36.", "The test router uses channel 40."]
    batch = await c.ingestion.begin("test-user", str(uuid4()), 1, len(contents))
    accepted, rejected, _ = await c.ingestion.append(
        "test-user",
        batch.id,
        1,
        [
            {"content": content, "kind": "configuration", "origin": "user", **extra}
            for content in contents
        ],
    )
    assert accepted == len(contents) and rejected == []
    return (await c.ingestion.commit("test-user", batch.id)).assertion_ids


def update(assertion_id, revision=1, **changes):
    return MetadataUpdate(
        assertion_id=assertion_id,
        expected_revision=revision,
        idempotency_key=str(uuid4()),
        changes=changes,
    )


async def test_metadata_preview_retry_and_concurrency(integration_container):
    c = integration_container
    ident = (await seed(c))[0]
    before = await c.search.get(ident, purpose="maintenance")
    request = update(
        ident,
        confidence=0.45,
        observed_at=NOW.isoformat(),
        evidence_state="reported",
        temporal_role="ongoing",
        facet="wifi.channel",
        provenance=[{"kind": "command", "locator": "observation:router-status"}],
    )
    preview = await c.maintenance.update("test-user", request.model_copy(update={"dry_run": True}))
    assert preview.dry_run and preview.revision == 2
    assert (await c.search.get(ident, purpose="maintenance")).revision == 1
    applied = await c.maintenance.update("test-user", request)
    assert applied.revision == 2 and not applied.replayed
    assert (await c.maintenance.update("test-user", request)).replayed
    with pytest.raises(ConflictError):
        await c.maintenance.update(
            "test-user",
            request.model_copy(
                update={"changes": request.changes.model_copy(update={"confidence": 0.3})}
            ),
        )
    after = await c.search.get(ident, purpose="maintenance")
    assert after.confidence == 0.45 and after.observed_at == NOW
    assert after.last_verified_at is None and after.evidence_state == "reported"
    assert after.content == before.content and after.last_confirmed_at == before.last_confirmed_at
    competing = [update(ident, 2, confidence=x) for x in (0.2, 0.3)]
    results = await asyncio.gather(
        *(c.maintenance.update("test-user", r) for r in competing), return_exceptions=True
    )
    assert sum(isinstance(r, ConflictError) for r in results) == 1
    assert (await c.search.get(ident, purpose="maintenance")).revision == 3


async def test_source_metadata_can_be_repaired_and_verified(integration_container):
    c = integration_container
    ident = (await seed(c, sources=[{"url": "https://example.com/product"}]))[0]
    result = await c.maintenance.update(
        "test-user",
        update(
            ident,
            sources=[
                {
                    "url": "https://example.com/product",
                    "title": "Observed product",
                    "retrieved_at": NOW.isoformat(),
                }
            ],
            evidence_state="verified",
            last_verified_at=NOW.isoformat(),
        ),
    )
    after = await c.search.get(ident, purpose="maintenance")
    assert result.revision == 2 and after.sources[0].retrieved_at == NOW
    assert after.evidence_state == "verified" and after.last_verified_at == NOW
    await c.maintenance.update(
        "test-user", update(ident, 2, sources=[], evidence_state="reported", last_verified_at=None)
    )
    assert not (await c.search.get(ident, purpose="maintenance")).sources


async def test_invalid_metadata_and_references(integration_container):
    c = integration_container
    ident = (await seed(c))[0]
    for changes in (
        {},
        {"evidence_state": "verified"},
        {"workflow_state": "completed"},
        {"valid_from": NOW.isoformat(), "valid_to": (NOW - timedelta(days=1)).isoformat()},
        {"context_id": str(uuid4())},
        {"confidence": None},
        {"sources": [{"url": "https://user:password@example.com"}]},
    ):
        with pytest.raises((InvalidRequestError, ValidationError)):
            await c.maintenance.update("test-user", update(ident, **changes))
    with pytest.raises(NotFoundError):
        await c.maintenance.update("test-user", update(uuid4(), confidence=0.5))
    artifact = await c.artifacts.begin(
        "test-user", "provenance-artifact", "note.txt", "text/plain", 1
    )
    await c.artifacts.append("test-user", artifact.id, 1, "Synthetic evidence note.")
    committed = await c.artifacts.commit("test-user", artifact.id)
    with pytest.raises(InvalidRequestError):
        await c.maintenance.update(
            "test-user",
            update(ident, provenance=[{"kind": "artifact", "locator": str(committed.artifact_id)}]),
        )
    # Same normalized text with new metadata reports ignored fields rather than replacing it.
    batch = await c.ingestion.begin("test-user", "duplicate-metadata", 1, 1)
    await c.ingestion.append(
        "test-user",
        batch.id,
        1,
        [
            {
                "content": "The test router uses channel 36.",
                "kind": "configuration",
                "origin": "user",
                "evidence_state": "reported",
            }
        ],
    )
    result = await c.ingestion.commit("test-user", batch.id)
    assert "evidence_state" in result.items[0].ignored_fields


async def test_usage_counts_purpose_retries_and_concurrent_reads(integration_container):
    c = integration_container
    ids = await seed(c)
    original = await c.search.get(ids[0], purpose="maintenance")
    assert original.usage.retrieval_count == 0
    event = uuid4()
    for _ in range(2):
        await c.search.search("router", purpose="normal", event_id=event, principal="test-user")
    for _ in range(2):
        await c.search.get(ids[0], purpose="normal", event_id=event, principal="test-user")
    await c.search.check_candidates(["The test router uses channel 36."])
    await c.search.search("router", purpose="maintenance")
    after = await c.search.get(ids[0], purpose="maintenance")
    assert after.usage.retrieval_count == 2 and after.usage.explicit_fetch_count == 1
    assert after.revision == original.revision and after.updated_at == original.updated_at
    assert after.last_confirmed_at == original.last_confirmed_at and after.usage.use_count == 0
    await asyncio.gather(*(c.search.get(ids[0]) for _ in range(12)))
    assert (await c.search.get(ids[0], purpose="maintenance")).usage.retrieval_count == 14
    used_event = uuid4()
    assert (
        await c.usage.record(ids, principal="test-user", event_id=used_event, operation="use") == 2
    )
    assert (
        await c.usage.record(ids, principal="test-user", event_id=used_event, operation="use") == 0
    )
    assert (await c.search.get(ids[0], purpose="maintenance")).usage.use_count == 1
    with pytest.raises(ConflictError):
        await c.usage.record([ids[0]], principal="test-user", event_id=used_event, operation="use")
    with pytest.raises(NotFoundError):
        await c.usage.record([uuid4()], operation="use")
    with pytest.raises(InvalidRequestError):
        await c.usage.record(ids, operation="bad")
    assert await c.usage.record([]) == 0
    with pytest.raises(NotFoundError):
        await c.search.get(uuid4())
    with pytest.raises(InvalidRequestError):
        await c.search.get(ids[0], purpose="wrong")


async def test_telemetry_failure_does_not_fail_reads(integration_container, monkeypatch):
    from knowledge_vault.services.maintenance import UsageService

    c = integration_container
    ident = (await seed(c))[0]

    async def failed(*args, **kwargs):
        raise RuntimeError("synthetic telemetry outage")

    monkeypatch.setattr(UsageService, "record", failed)
    result = await c.search.get(ident)
    assert result.id == ident and result.usage.retrieval_count == 0
    assert not result.usage.available


async def test_context_links_order_cycle_resolution_and_deletion(integration_container):
    c = integration_container
    context = ContextInput(id=uuid4(), name="Synthetic kitchen oven", aliases=["test oven"])
    assert (await c.maintenance.context(context)).id == context.id
    assert (await c.maintenance.context(context)).id == context.id
    assert (await c.maintenance.get_context(context.id)).aliases == ["test oven"]
    with pytest.raises(ConflictError):
        await c.maintenance.context(context.model_copy(update={"name": "Different"}))
    with pytest.raises(NotFoundError):
        await c.maintenance.get_context(uuid4())
    ids = await seed(
        c,
        ["Synthetic recipe parent.", "Dry the surface first.", "Apply heat second."],
        kind="procedure",
        context_id=str(context.id),
        temporal_role="historical",
    )
    for index in (1, 2):
        link = LinkInput(source_id=ids[index], target_id=ids[0], kind="part_of", position=index)
        assert await c.maintenance.link(link) == await c.maintenance.link(link)
    related = await c.maintenance.related(ids[0], limit=1)
    assert related.truncated and related.links[0].source_id == ids[1]
    assert len((await c.maintenance.related(ids[0])).links) == 2
    with pytest.raises(ConflictError):
        await c.maintenance.link(LinkInput(source_id=ids[0], target_id=ids[1], kind="part_of"))
    with pytest.raises(ConflictError):
        await c.maintenance.link(
            LinkInput(source_id=ids[2], target_id=ids[0], kind="part_of", position=1)
        )
    with pytest.raises(NotFoundError):
        await c.maintenance.link(LinkInput(source_id=uuid4(), target_id=ids[0], kind="supports"))
    with pytest.raises(InvalidRequestError):
        await c.maintenance.link(LinkInput(source_id=ids[1], target_id=ids[0], kind="resolves"))
    with pytest.raises(InvalidRequestError):
        await c.maintenance.related(ids[0], 0)
    with pytest.raises(NotFoundError):
        await c.maintenance.related(uuid4())
    q = (
        await seed(
            c,
            ["Was the synthetic procedure tested?"],
            kind="open_question",
            workflow_state="unresolved",
        )
    )[0]
    with pytest.raises(InvalidRequestError):
        await c.maintenance.update("test-user", update(q, workflow_state="resolved"))
    await c.maintenance.link(LinkInput(source_id=q, target_id=ids[0], kind="resolves"))
    assert (await c.search.get(q, purpose="maintenance")).workflow_state == "resolved"
    equivalent = LinkInput(source_id=ids[2], target_id=ids[1], kind="equivalent_to")
    assert await c.maintenance.link(equivalent) == await c.maintenance.link(
        equivalent.model_copy(update={"source_id": ids[1], "target_id": ids[2]})
    )
    await c.search.get(ids[0])
    preview = await c.administration.preview_forgetting("test-user", [*ids, q])
    await c.administration.confirm_forgetting("test-user", preview["confirmation_token"])
    async with c.database.sessions() as session:
        for table in (AssertionLinkRow, AssertionUsageRow, MaintenanceEventRow):
            assert await session.scalar(select(func.count()).select_from(table)) == 0


async def test_aliases_review_and_competing_candidates(integration_container):
    c = integration_container
    context = ContextInput(id=uuid4(), name="Synthetic network", aliases=[])
    await c.maintenance.context(context)
    ids = await seed(
        c,
        topics=["router"],
        context_id=str(context.id),
        facet="channel",
        temporal_role="ongoing",
        review_after=(NOW - timedelta(days=1)).isoformat(),
    )
    alias = TopicAliasInput(alias="маршрутизатор", canonical="router")
    assert await c.maintenance.alias(alias) == await c.maintenance.alias(alias)
    assert (await c.maintenance.aliases()).aliases[alias.alias] == "router"
    for invalid in (
        TopicAliasInput(alias="router", canonical="router"),
        TopicAliasInput(alias="маршрутизатор", canonical="network"),
        TopicAliasInput(alias="router", canonical="network"),
    ):
        with pytest.raises((InvalidRequestError, ConflictError)):
            await c.maintenance.alias(invalid)
    page = await c.search.search(
        "маршрутизатор", SearchFilters(topics=["маршрутизатор"]), purpose="maintenance"
    )
    assert {h.assertion.id for h in page.results} == set(ids)
    page = await c.search.search(
        "router",
        SearchFilters(
            context_id=context.id,
            facet="channel",
            temporal_roles=["ongoing"],
            evidence_states=["unknown"],
            review_due=True,
        ),
        purpose="maintenance",
    )
    assert len(page.results) == 2
    review = await c.maintenance.review(limit=1, context_id=context.id)
    assert review.next_after and "review_due" in review.items[0].reasons
    assert "missing_observation_date" in review.items[0].reasons
    assert len((await c.maintenance.review(after=review.next_after)).items) == 1
    candidates = await c.maintenance.candidates(context_id=context.id, limit=1)
    assert candidates.candidates[0].reason == "competing_state"
    assert (await c.maintenance.candidates(topic="router")).inspected == 2
    await seed(c, ["The synthetic product costs ten coins."], kind="external_fact")
    review = await c.maintenance.review()
    assert any("missing_provenance" in r.reasons for r in review.items)
    for operation in (c.maintenance.review, c.maintenance.candidates):
        with pytest.raises(InvalidRequestError):
            await operation(limit=0)


async def test_legacy_updates_increment_revision_but_usage_does_not(integration_container):
    c = integration_container
    ident = (await seed(c))[0]
    async with c.database.sessions.begin() as session:
        row = await session.get(AssertionRow, ident)
        row.confidence = 0.75
    view = await c.search.get(ident)
    assert view.revision == 2 and view.evidence_state == "unknown"
    assert view.usage.retrieval_count == 1
    async with c.database.sessions.begin() as session:
        row = await session.get(AssertionRow, ident)
        row.embedding_state = "failed"
    assert (await c.search.get(ident, purpose="maintenance")).revision == 2


async def test_expired_receipts_and_similar_candidates(integration_container):
    c = integration_container
    ids = await seed(
        c,
        [
            "Synthetic cooking step says dry the surface first.",
            "Synthetic cooking step says dry the surface second.",
        ],
        topics=["cooking"],
    )
    candidate = await c.maintenance.candidates(topic="cooking")
    assert candidate.candidates[0].reason == "similar_text"
    request = update(ids[0], confidence=0.7)
    await c.maintenance.update("test-user", request)
    async with c.database.sessions.begin() as session:
        for row in await session.scalars(select(MaintenanceEventRow)):
            row.expires_at = NOW - timedelta(days=2)
    assert (
        await c.maintenance.update("test-user", update(ids[0], 2, confidence=0.6))
    ).revision == 3
    await c.usage.record(ids)
    assert len(await c.usage.views(ids)) == 2
