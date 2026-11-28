"""Persisted pre-upgrade Unicode hashes still deduplicate, retrieve and replay exactly."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from knowledge_vault.persistence.tables import AssertionRow

pytestmark = pytest.mark.integration


async def test_legacy_casefold_hash_still_matches_candidates_and_commit_replay(
    integration_container,
):
    container = integration_container
    identifier = uuid4()
    legacy_hash = "dfb14f4b37f4b5b677523ff90f819c14ccbe8bffd7744781d5175af3b70d152f"
    async with container.database.sessions() as session, session.begin():
        session.add(
            AssertionRow(
                id=identifier,
                content="\u1e96",
                normalized_content="h\u0331",
                content_hash=legacy_hash,
                kind="user_fact",
                origin="user",
                confidence=1.0,
            )
        )
    batch = await container.ingestion.begin("test-user", "legacy-unicode-hash", 1, 2)
    await container.ingestion.append(
        "test-user",
        batch.id,
        1,
        [
            {"content": text, "kind": "user_fact", "origin": "user"}
            for text in ("\u1e96", "h\u0331")
        ],
    )
    result = await container.ingestion.commit("test-user", batch.id)
    assert result.counts.inserted == 0
    assert result.counts.confirmed_existing == 2
    assert set(result.assertion_ids) == {identifier}
    assert await container.ingestion.commit("test-user", batch.id) == result
    candidates = await container.search.check_candidates(["\u1e96", "h\u0331"])
    assert all(
        any(m.id == identifier and m.exact for m in page.matches) for page in candidates.results
    )
    async with container.database.sessions() as session:
        row = (await session.scalars(select(AssertionRow))).one()
        assert row.id == identifier and row.content_hash == legacy_hash
        assert row.normalized_content == "h\u0331"
