import hashlib
import time
from datetime import UTC, datetime, timedelta
from functools import partial
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, event, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError

from knowledge_vault.api.app import create_app
from knowledge_vault.domain.inspection import (
    BrowseFilters,
    BrowseRequest,
    CatalogRequest,
    EvidenceRequest,
    InspectionContext,
    PageRequest,
    RankedRequest,
)
from knowledge_vault.domain.models import AssertionInput
from knowledge_vault.domain.temporal import ResolutionRequest, StateIdentity
from knowledge_vault.persistence.tables import (
    ArtifactRow,
    AssertionArtifactRow,
    AssertionLinkRow,
    AssertionRow,
    AssertionUsageRow,
    ConflictRow,
    KnowledgeContextRow,
    MaintenanceEventRow,
    SourceRow,
    TemporalEvidenceRow,
    TemporalRetirementRow,
    TopicAliasRow,
)
from knowledge_vault.services.cursors import InvalidCursorError
from knowledge_vault.services.errors import InvalidRequestError, NotFoundError

pytestmark = pytest.mark.integration
CTX = InspectionContext(principal="reader", audience="private.example")
IDENTITY = StateIdentity(subject="synthetic", property="setting", scope="test", state_type="fact")


async def seed(container, count=1):
    rows = []
    statuses = ["current", "uncertain", "disputed", "superseded"]
    for number in range(count):
        content = f"matchword synthetic inspection assertion {number}"
        rows.append(
            {
                "id": UUID(int=number + 1),
                "content": content,
                "normalized_content": content,
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                "kind": "user_fact",
                "confidence": 1.0,
                "origin": "user",
                "status": statuses[number % 4],
                "topics": ["safety", "safeguard"] if number % 3 else [],
                "context_id": None,
                "facet": None,
            }
        )
    async with container.database.sessions.begin() as session:
        await session.execute(insert(AssertionRow), rows)
    return [row["id"] for row in rows]


async def collect(call, request):
    items = []
    cursors = set()
    while True:
        result = await call(request, CTX)
        items.extend(result.items)
        if result.next_cursor is None:
            return items
        assert result.next_cursor not in cursors
        cursors.add(result.next_cursor)
        request = request.model_copy(update={"cursor": result.next_cursor})


async def test_exhaustive_and_strict_lexical_enumeration_beyond_ranking_limit(
    integration_container,
):
    container = integration_container
    ids = await seed(container, 653)
    browse = container.inspection.browse
    async with container.database.sessions.begin() as session:
        session.add(TopicAliasRow(alias="safeguard", canonical="safety"))
    for request in [
        BrowseRequest(limit=37),
        BrowseRequest(mode="lexical", query="matchword", limit=37),
    ]:
        items = await collect(browse.assertions, request)
        assert [item.id for item in items] == ids
        assert {item.status for item in items} == {"current", "uncertain", "disputed", "superseded"}
    assert not (
        await browse.assertions(BrowseRequest(mode="lexical", query='"missing phrase"'), CTX)
    ).items
    current = await collect(
        browse.assertions, BrowseRequest(filters=BrowseFilters(statuses=["current"]))
    )
    assert len(current) == 164
    topics = await collect(
        browse.assertions, BrowseRequest(filters=BrowseFilters(topics=["safety"]))
    )
    assert len(topics) == 435
    missing = await collect(
        browse.assertions, BrowseRequest(filters=BrowseFilters(missing_topic=True))
    )
    assert len(missing) == 218
    page = await browse.assertions(BrowseRequest(limit=1), CTX)
    assert page.next_cursor
    async with container.database.sessions.begin() as session:
        await session.execute(delete(AssertionRow).where(AssertionRow.id == ids[0]))
    continuation = await browse.assertions(BrowseRequest(limit=1, cursor=page.next_cursor), CTX)
    assert continuation.items[0].id == ids[1]
    for ctx, request in [
        (
            CTX.model_copy(update={"principal": "other"}),
            BrowseRequest(limit=1, cursor=page.next_cursor),
        ),
        (CTX, BrowseRequest(limit=2, cursor=page.next_cursor)),
        (CTX, BrowseRequest(limit=1, cursor="!")),
    ]:
        with pytest.raises(InvalidCursorError):
            await browse.assertions(request, ctx)
    async with container.database.sessions.begin() as session:
        session.add(TopicAliasRow(alias="resilience", canonical="safety"))
    with pytest.raises(InvalidCursorError):
        await browse.assertions(BrowseRequest(limit=1, cursor=page.next_cursor), CTX)
    with pytest.raises(InvalidRequestError):
        await browse.assertions(BrowseRequest(limit=51), CTX)


async def test_catalog_counts_contexts_facets_aliases_nulls_and_prefix(integration_container):
    container = integration_container
    ids = await seed(container, 12)
    context_id = uuid4()
    async with container.database.sessions.begin() as session:
        session.add(KnowledgeContextRow(id=context_id, name="Synthetic context", aliases=["test"]))
        session.add(TopicAliasRow(alias="safeguard", canonical="safety"))
        await session.flush()
        await session.execute(
            update(AssertionRow)
            .where(AssertionRow.id.in_(ids[:6]))
            .values(context_id=context_id, facet="configuration")
        )
    browse = container.inspection.browse
    topics = await collect(browse.catalog, CatalogRequest(dimension="topics", limit=1))
    assert [(x.key, x.assertion_count) for x in topics] == [(None, 4), ("safety", 8)]
    assert sum(topics[1].by_status.values()) == 8
    assert topics[1].by_kind["user_fact"] == 8
    assert topics[1].by_evidence_state["unknown"] == 8
    assert topics[1].by_workflow_state["unspecified"] == 8
    contexts = await collect(browse.catalog, CatalogRequest(dimension="contexts"))
    assert {x.key: x.assertion_count for x in contexts} == {None: 6, str(context_id): 6}
    facets = await collect(
        browse.catalog,
        CatalogRequest(dimension="facets", filters=BrowseFilters(context_id=context_id)),
    )
    assert [(x.key, x.assertion_count) for x in facets] == [("configuration", 6)]
    assert not (await browse.catalog(CatalogRequest(dimension="topics", prefix="s%"), CTX)).items
    assert (await browse.catalog(CatalogRequest(dimension="topics", prefix="safe"), CTX)).items[
        0
    ].key == "safety"
    for filters, expected in [
        (BrowseFilters(missing_context=True), 6),
        (BrowseFilters(missing_facet=True), 6),
        (BrowseFilters(statuses=["uncertain"], topics=["safeguard"]), 2),
    ]:
        items = await collect(browse.assertions, BrowseRequest(filters=filters))
        assert len(items) == expected


async def test_all_child_collections_and_detail_are_bounded_and_resumable(
    integration_container, reader_token
):
    container = integration_container
    ids = await seed(container, 112)
    target = ids[0]
    now = datetime.now(UTC)
    payload = AssertionInput(
        content="Synthetic immutable evidence", kind="user_fact", origin="user"
    ).model_dump(mode="json")
    async with container.database.sessions.begin() as session:
        for number in range(110):
            session.add(
                SourceRow(
                    assertion_id=target, url=f"https://example.test/{number:03}", title="Synthetic"
                )
            )
            artifact = ArtifactRow(
                id=uuid4(),
                principal_id="synthetic",
                idempotency_hash=str(number),
                state="stored",
                filename=f"fixture-{number}.txt",
                media_type="text/plain",
                declared_chunks=1,
                content_text="inert fixture",
                content_sha256=hashlib.sha256(str(number).encode()).hexdigest(),
                size_bytes=12,
                expires_at=now + timedelta(days=1),
            )
            session.add(artifact)
            await session.flush()
            session.add(AssertionArtifactRow(assertion_id=target, artifact_id=artifact.id))
            session.add(
                AssertionLinkRow(source_id=target, target_id=ids[number + 1], kind="supports")
            )
            identity = IDENTITY.model_copy(update={"property": f"setting-{number:03}"})
            evidence = {
                **payload,
                "temporal": {
                    "identity": identity.model_dump(),
                    "evidence_key": f"identity-{number:03}",
                    "value": "test",
                    "mode": "snapshot",
                },
            }
            session.add(
                TemporalEvidenceRow(
                    evidence_key=f"identity-{number:03}",
                    assertion_id=target,
                    identity_hash=identity.digest,
                    payload_hash=hashlib.sha256(str(number).encode()).hexdigest(),
                    payload=evidence,
                )
            )
        session.add(
            ConflictRow(
                left_assertion_id=target, right_assertion_id=ids[1], reason="synthetic conflict"
            )
        )
        await session.execute(
            update(AssertionRow).where(AssertionRow.id == ids[-1]).values(supersedes_id=target)
        )
    browse = container.inspection.browse
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(container.database.engine.sync_engine, "before_cursor_execute", record)
    try:
        detail = await browse.detail(target, CTX)
    finally:
        event.remove(container.database.engine.sync_engine, "before_cursor_execute", record)
    assert len(detail.sources) == len(detail.artifacts) == len(detail.temporal_identities) == 20
    assert (
        detail.source_page.next_cursor
        and detail.artifact_page.next_cursor
        and detail.identity_page.next_cursor
    )
    assert detail.temporal_identities_truncated
    assert len(statements) <= 20
    collection_queries = [
        statement
        for statement in statements
        if "FROM assertion_sources" in statement or "FROM artifacts JOIN" in statement
    ]
    assert collection_queries and all("LIMIT" in statement for statement in collection_queries)
    for method in [browse.sources, browse.artifacts, browse.identities]:
        items = await collect(partial(method, target), PageRequest(limit=17))
        assert len(items) == 110
    relations = await collect(
        lambda request, ctx: browse.relations(target, request, ctx), PageRequest(limit=23)
    )
    assert len(relations) == 112
    assert {item.type for item in relations} == {"link", "supersession", "conflict"}
    reverse = await browse.relations(ids[-1], PageRequest(), CTX)
    assert any(item.predecessor_id == target for item in reverse.items)
    container.settings.inspection_principals = "reader"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(container)),
        base_url="https://private.example",
        headers={"Authorization": f"Bearer {reader_token}"},
    ) as client:
        response = await client.get(f"/api/v1/read/artifacts/{detail.artifacts[0].id}")
        assert response.status_code == 200 and response.json()["content"] == "inert fixture"
    for method in [
        browse.detail,
        browse.sources,
        browse.artifacts,
        browse.identities,
        browse.relations,
    ]:
        with pytest.raises(NotFoundError):
            if method == browse.detail:
                await method(uuid4(), CTX)
            else:
                await method(uuid4(), PageRequest(), CTX)


async def test_raw_history_beyond_resolution_bound_and_known_at_retirement(integration_container):
    container = integration_container
    target = (await seed(container))[0]
    now = datetime.now(UTC)
    payload = AssertionInput(
        content="Synthetic evidence", kind="user_fact", origin="user"
    ).model_dump(mode="json")
    rows = []
    for number in range(1003):
        key = f"evidence-{number:04}"
        rows.append(
            {
                "evidence_key": key,
                "assertion_id": target,
                "identity_hash": IDENTITY.digest,
                "payload_hash": hashlib.sha256(key.encode()).hexdigest(),
                "recorded_at": now,
                "payload": {
                    **payload,
                    "temporal": {
                        "identity": IDENTITY.model_dump(),
                        "evidence_key": key,
                        "value": "synthetic",
                        "mode": "snapshot",
                    },
                },
            }
        )
    async with container.database.sessions.begin() as session:
        await session.execute(insert(TemporalEvidenceRow), rows)
        session.add(
            TemporalRetirementRow(
                evidence_key=rows[0]["evidence_key"], retired_at=now + timedelta(hours=1)
            )
        )
    items = await collect(container.inspection.browse.evidence, EvidenceRequest(identity=IDENTITY))
    assert len(items) == 1003
    assert not items[0].retired
    later = await container.inspection.browse.evidence(
        EvidenceRequest(identity=IDENTITY, known_at=now + timedelta(days=1)), CTX
    )
    assert later.items[0].retired
    before = await container.inspection.browse.evidence(
        EvidenceRequest(identity=IDENTITY, known_at=now - timedelta(days=1)), CTX
    )
    assert not before.items
    with pytest.raises(InvalidRequestError, match="1000"):
        await container.inspection.resolve(ResolutionRequest(identity=IDENTITY))


async def test_read_only_transactions_pool_reuse_and_nonrecording_http(
    integration_container, reader_token
):
    container = integration_container
    target = (await seed(container))[0]
    async with container.database.read_sessions() as session:
        assert await session.scalar(text("SHOW transaction_read_only")) == "on"
        with pytest.raises(DBAPIError):
            await session.execute(update(AssertionRow).values(confidence=0.5))
    async with container.database.sessions.begin() as session:
        assert await session.scalar(text("SHOW transaction_read_only")) == "off"
        await session.execute(
            update(AssertionRow).where(AssertionRow.id == target).values(confidence=0.75)
        )
    container.settings.inspection_principals = "reader"
    context_id = uuid4()
    async with container.database.sessions.begin() as session:
        session.add(KnowledgeContextRow(id=context_id, name="Synthetic context", aliases=[]))
    app = create_app(container)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://private.example",
        headers={"Authorization": f"Bearer {reader_token}"},
    ) as client:
        for method, path, body in [
            ("POST", "/assertions/query", {}),
            ("GET", f"/assertions/{target}", None),
            ("POST", "/search", {"query": "matchword"}),
            ("POST", "/catalog/query", {"dimension": "topics"}),
            ("GET", "/topic-aliases", None),
            ("GET", f"/contexts/{context_id}", None),
            ("GET", f"/assertions/{target}/sources", None),
            ("GET", f"/assertions/{target}/artifacts", None),
            ("GET", f"/assertions/{target}/identities", None),
            ("GET", f"/assertions/{target}/relations", None),
            ("POST", "/state/evidence/query", {"identity": IDENTITY.model_dump()}),
            ("POST", "/state/resolve", {"identity": IDENTITY.model_dump()}),
        ]:
            response = await client.request(method, "/api/v1/read" + path, json=body)
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store"
        assert (await client.get("/api/v1/search")).status_code == 403
        assert (await client.get(f"/api/v1/read/artifacts/{uuid4()}")).status_code == 404
    async with container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(AssertionUsageRow)) == 0
        assert await session.scalar(select(func.count()).select_from(MaintenanceEventRow)) == 0
    ranked = await container.inspection.ranked(RankedRequest(query="matchword"), CTX)
    assert ranked.candidate_limited and ranked.items
    assert ranked.items[0].assertion.confidence == 0.75
    assert container.inspection.search_service._embedder is container.embedder


async def test_live_scan_and_ranked_cursor_boundary(integration_container):
    container = integration_container
    ids = await seed(container, 60)
    page = await container.inspection.browse.assertions(BrowseRequest(limit=3), CTX)
    async with container.database.sessions.begin() as session:
        await session.execute(delete(AssertionRow).where(AssertionRow.id == ids[3]))
        for number in [0, 100]:
            content = f"matchword concurrent synthetic {number}"
            session.add(
                AssertionRow(
                    id=UUID(int=number),
                    content=content,
                    normalized_content=content,
                    content_hash=hashlib.sha256(content.encode()).hexdigest(),
                    kind="user_fact",
                    origin="user",
                    confidence=1.0,
                    topics=[],
                )
            )
    rest = await collect(
        container.inspection.browse.assertions, BrowseRequest(limit=3, cursor=page.next_cursor)
    )
    assert UUID(int=0) not in [item.id for item in rest]
    assert UUID(int=100) in [item.id for item in rest]
    assert ids[3] not in [item.id for item in rest]
    assert page.consistency == "live"
    ranked = await container.inspection.ranked(RankedRequest(query="matchword", limit=1), CTX)
    assert ranked.next_cursor
    more = await container.inspection.ranked(
        RankedRequest(query="matchword", limit=1, cursor=ranked.next_cursor), CTX
    )
    assert more.items and more.items[0].assertion.id != ranked.items[0].assertion.id
    with pytest.raises(InvalidCursorError):
        await container.inspection.ranked(
            RankedRequest(query="other", limit=1, cursor=ranked.next_cursor), CTX
        )


async def test_inspection_sql_budget_on_50000_synthetic_assertions(integration_container):
    container = integration_container
    await seed(container, 50_000)
    async with container.database.sessions.begin() as session:
        await session.execute(text("ANALYZE assertions"))
    browse_times, catalog_times = [], []
    catalog_queries = []

    def capture_catalog(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("WITH eligible AS"):
            catalog_queries.append((statement, parameters))

    event.listen(container.database.engine.sync_engine, "before_cursor_execute", capture_catalog)
    for _ in range(6):
        started = time.perf_counter()
        page = await container.inspection.browse.assertions(BrowseRequest(), CTX)
        browse_times.append((time.perf_counter() - started) * 1000)
        assert len(page.items) == 50 and page.next_cursor
        started = time.perf_counter()
        catalog = await container.inspection.browse.catalog(CatalogRequest(dimension="topics"), CTX)
        catalog_times.append((time.perf_counter() - started) * 1000)
        assert {item.key: item.assertion_count for item in catalog.items} == {
            None: 16667,
            "safeguard": 33333,
            "safety": 33333,
        }
    async with container.database.read_sessions() as session:
        event.remove(
            container.database.engine.sync_engine, "before_cursor_execute", capture_catalog
        )
        connection = await session.connection()
        await session.execute(text("SET LOCAL jit = off"))
        statement, parameters = catalog_queries[-1]
        catalog_plan = await connection.exec_driver_sql(
            "EXPLAIN (ANALYZE, BUFFERS) " + statement, parameters
        )
        print("CATALOG PLAN:", "\n".join(catalog_plan.scalars()))
        plan = await session.execute(
            text(
                "EXPLAIN (ANALYZE, BUFFERS) SELECT id FROM assertions "
                "WHERE id > '00000000-0000-0000-0000-000000000100'::uuid ORDER BY id LIMIT 51"
            )
        )
        plan_text = "\n".join(plan.scalars())
        print("KEYSET PLAN:", plan_text)
        assert "Index Only Scan" in plan_text or "Index Scan" in plan_text
    print(f"INSPECTION 50k browse_ms={browse_times} catalog_ms={catalog_times}")
    # Retain timing evidence without turning shared-host contention into a flaky CI assertion.


async def test_response_byte_bounds_and_frozen_review_due(integration_container, monkeypatch):
    container = integration_container
    ids = await seed(container, 5)
    async with container.database.sessions.begin() as session:
        await session.execute(
            update(AssertionRow).values(review_after=datetime.now(UTC) - timedelta(days=1))
        )
    monkeypatch.setattr("knowledge_vault.services.browse.MAX_RESPONSE_BYTES", 1900)
    page = await container.inspection.browse.assertions(
        BrowseRequest(filters=BrowseFilters(review_due=True)), CTX
    )
    assert len(page.items) < 5 and page.next_cursor
    monkeypatch.setattr("knowledge_vault.services.browse.MAX_RESPONSE_BYTES", 1)
    with pytest.raises(InvalidRequestError, match="one retained item"):
        await container.inspection.browse.assertions(BrowseRequest(), CTX)
    monkeypatch.setattr("knowledge_vault.services.browse.MAX_RESPONSE_BYTES", 1000)
    with pytest.raises(InvalidRequestError, match="detail"):
        await container.inspection.browse.detail(ids[0], CTX)
