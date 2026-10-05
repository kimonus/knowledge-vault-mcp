import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

from knowledge_vault.mcp.server import create_mcp_server


@pytest.mark.contract
@pytest.mark.integration
async def test_exact_procedure_details_survive_commit_retry_and_full_readback(
    integration_container,
) -> None:
    server = create_mcp_server(integration_container)
    recipe = {
        "content": (
            "Bread procedure: mix 300 g flour, 210 g water, 6 g salt and 1 g yeast; "
            "knead for 8 minutes; ferment for 12 hours at 20°C; preheat to 230°C; "
            "bake for 25 minutes. Validate that the crust is browned without burning."
        ),
        "kind": "procedure",
        "origin": "user",
        "topics": ["bread"],
        "sources": [{"url": "https://example.org/bread", "title": "Bread recipe"}],
    }
    alternative = {
        "content": "A 30-minute bake at 230°C was rejected because it burned the bread crust.",
        "kind": "rejected_option",
        "origin": "user",
        "topics": ["bread"],
    }
    context = auth_context_var.set(
        AuthenticatedUser(
            AccessToken(
                token="synthetic",
                client_id="readback-test",
                subject="test-user",
                scopes=["knowledge:read", "knowledge:write"],
            )
        )
    )
    try:
        begun = await server.call_tool(
            "begin_knowledge_flush",
            {"idempotency_key": "recipe-readback", "declared_parts": 2, "declared_items": 3},
        )
        batch = {"batch_id": begun.structured_content["batch_id"]}
        for number, assertions in [(1, [recipe, recipe]), (2, [alternative])]:
            appended = await server.call_tool(
                "append_knowledge", {**batch, "part_number": number, "assertions": assertions}
            )
            assert not appended.is_error
        committed = (await server.call_tool("commit_knowledge_flush", batch)).structured_content
        replayed = (await server.call_tool("commit_knowledge_flush", batch)).structured_content
        assert replayed == committed
        assert committed["counts"]["inserted"] == 2
        assert committed["counts"]["confirmed_existing"] == 1
        assert committed["assertion_ids"][0] == committed["assertion_ids"][1]
        assert [(entry["index"], entry["outcome"]) for entry in committed["items"]] == [
            (0, "inserted"),
            (1, "confirmed_existing"),
            (2, "inserted"),
        ]
        assert [entry["assertion_id"] for entry in committed["items"]] == committed["assertion_ids"]
        assert all(entry["ignored_fields"] == [] for entry in committed["items"])
        assert committed["readback_ids"] == []
        records = {}
        for assertion_id in dict.fromkeys(committed["assertion_ids"]):
            result = await server.call_tool("get_knowledge", {"assertion_id": assertion_id})
            assert not result.is_error
            records[assertion_id] = result.structured_content
        for assertion_id, expected in zip(
            committed["assertion_ids"], [recipe, recipe, alternative], strict=True
        ):
            stored = records[assertion_id]
            for field in ("content", "kind", "origin", "topics"):
                assert stored[field] == expected[field]
            assert stored["untrusted_data"] is True
        stored_recipe = records[committed["assertion_ids"][0]]
        assert stored_recipe["sources"][0]["url"] == recipe["sources"][0]["url"]
        assert stored_recipe["sources"][0]["title"] == "Bread recipe"
    finally:
        auth_context_var.reset(context)
