"""Conjunctive question fallback retains lifecycle and pagination semantics."""

import pytest

from knowledge_vault.domain.models import SearchFilters
from knowledge_vault.services.search import SearchService

pytestmark = pytest.mark.integration


async def test_purchase_question_retrieves_purchases_without_considered_option(
    integration_container,
):
    c = integration_container
    batch = await c.ingestion.begin("test-user", "question-fallback-fixture", 1, 3)
    await c.ingestion.append(
        "test-user",
        batch.id,
        1,
        [
            {
                "content": "The user considered slate-a for a tablet.",
                "kind": "considered_option",
                "origin": "user",
            },
            {
                "content": "The user purchased slate-b as a tablet.",
                "kind": "user_fact",
                "origin": "user",
            },
            {
                "content": "The user purchased slate-c as a tablet.",
                "kind": "user_fact",
                "origin": "user",
            },
        ],
    )
    committed = await c.ingestion.commit("test-user", batch.id)
    search = SearchService(
        c.database.sessions, c.settings.model_copy(update={"embeddings_enabled": False}), None
    )

    question = "Which tablet did the user purchase?"
    first = await search.search(question, limit=1, purpose="maintenance")
    assert first.next_cursor
    second = await search.search(question, limit=1, cursor=first.next_cursor, purpose="maintenance")
    found = {first.results[0].assertion.id, second.results[0].assertion.id}
    assert found == set(committed.assertion_ids[1:])
    filtered = await search.search(
        question,
        SearchFilters(kinds=["considered_option"]),
        purpose="maintenance",
    )
    assert filtered.results == []
    assert len((await search.search("tablet", purpose="maintenance")).results) == 3
    missing = await search.search(
        "Which violin model did the user purchase?", purpose="maintenance"
    )
    assert missing.results == []
    assert (await search.search('"missing exact phrase"', purpose="maintenance")).results == []
