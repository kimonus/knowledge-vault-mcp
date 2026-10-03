import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.memory import create_client_server_memory_streams

from knowledge_vault.container import build_container
from knowledge_vault.embeddings.providers import DeterministicFakeProvider
from knowledge_vault.ingestion_policy import CORE_INSTRUCTIONS, DEFAULT_INGESTION_POLICY
from knowledge_vault.mcp.server import create_mcp_server


@pytest.mark.contract
async def test_tool_discovery_contract(settings) -> None:
    server = create_mcp_server(
        build_container(settings, embedder=DeterministicFakeProvider(dimensions=384))
    )
    tools = {tool.name: tool for tool in await server.list_tools()}
    expected = {
        "search",
        "fetch",
        "begin_knowledge_flush",
        "append_knowledge",
        "commit_knowledge_flush",
        "abort_knowledge_flush",
        "begin_knowledge_artifact",
        "append_knowledge_artifact",
        "commit_knowledge_artifact",
        "get_knowledge_artifact",
        "search_knowledge",
        "get_knowledge",
        "list_knowledge_conflicts",
        "correct_knowledge",
        "forget_knowledge",
        "get_knowledge_statistics",
    }
    assert set(tools) == expected
    assert tools["search"].input_schema["required"] == ["query"]
    assert set(tools["search"].input_schema["properties"]) == {"query"}
    assert tools["fetch"].input_schema["required"] == ["id"]
    assert set(tools["fetch"].input_schema["properties"]) == {"id"}
    assert set(tools["search"].output_schema["properties"]) == {"results"}
    assert set(tools["fetch"].output_schema["properties"]) == {
        "id",
        "title",
        "text",
        "url",
        "metadata",
    }
    assert tools["search"].annotations.read_only_hint is True
    assert tools["begin_knowledge_flush"].annotations.destructive_hint is False
    assert tools["forget_knowledge"].annotations.destructive_hint is True
    assert all(tool.annotations.open_world_hint is False for tool in tools.values())


@pytest.mark.contract
@pytest.mark.parametrize("custom", [False, True])
async def test_initialization_delivers_policy_over_mcp_transport(settings, custom: bool) -> None:
    policy = "Preserve exact quantities.\nKeep units and ordered steps."
    if custom:
        settings = settings.model_copy(
            update={"ingestion_policy": policy, "ingestion_policy_version": "recipe-2"}
        )
    container = build_container(settings, embedder=DeterministicFakeProvider(dimensions=384))
    server = create_mcp_server(container)
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(
            server._lowlevel_server.run,
            *server_streams,
            server._lowlevel_server.create_initialization_options(),
        )
        async with ClientSession(*client_streams) as client:
            initialization = await client.initialize()
            instructions = initialization.instructions
            assert instructions is not None
            assert instructions.startswith(CORE_INSTRUCTIONS)
            assert "get_knowledge" in instructions[:512]
            if custom:
                assert instructions.endswith(policy + "\n")
                assert "Operator extraction policy version: recipe-2" in instructions
                assert DEFAULT_INGESTION_POLICY not in instructions
            else:
                assert instructions.endswith(DEFAULT_INGESTION_POLICY + "\n")
        tasks.cancel_scope.cancel()
    await container.database.close()


@pytest.mark.contract
def test_openapi_contains_only_http_routes(settings) -> None:
    from knowledge_vault.api.app import create_app

    app = create_app(build_container(settings, embedder=DeterministicFakeProvider(dimensions=384)))
    schema = app.openapi()
    assert "/api/v1/flushes" in schema["paths"]
    assert "/api/v1/search" in schema["paths"]
    assert "/mcp" not in schema["paths"]


@pytest.mark.contract
@pytest.mark.integration
async def test_authenticated_mcp_tool_lifecycle(integration_container) -> None:
    server = create_mcp_server(integration_container)

    with pytest.raises(ToolError, match="authentication is required"):
        await server.call_tool("search", {"query": "anything"})

    access_token = AccessToken(
        token="synthetic",
        client_id="mcp-test",
        subject="test-user",
        scopes=["knowledge:read", "knowledge:write", "knowledge:admin"],
    )
    context_token = auth_context_var.set(AuthenticatedUser(access_token))
    try:
        begin = await server.call_tool(
            "begin_knowledge_flush",
            {"idempotency_key": "mcp-lifecycle", "declared_parts": 1, "declared_items": 1},
        )
        batch_id = begin.structured_content["batch_id"]
        appended = await server.call_tool(
            "append_knowledge",
            {
                "batch_id": batch_id,
                "part_number": 1,
                "assertions": [
                    {
                        "content": "The user enjoys multilingual MCP tools.",
                        "kind": "preference",
                        "origin": "user",
                        "topics": ["mcp"],
                    }
                ],
            },
        )
        assert appended.structured_content["accepted"] == 1
        committed = await server.call_tool("commit_knowledge_flush", {"batch_id": batch_id})
        assertion_id = committed.structured_content["assertion_ids"][0]

        upload = await server.call_tool(
            "begin_knowledge_artifact",
            {
                "idempotency_key": "mcp-artifact",
                "filename": "tools.csv",
                "media_type": "text/csv",
                "declared_chunks": 2,
            },
        )
        artifact_id = upload.structured_content["artifact_id"]
        for number, text in ((1, "tool,language\n"), (2, "search,pl\n")):
            chunk = await server.call_tool(
                "append_knowledge_artifact",
                {"artifact_id": artifact_id, "chunk_number": number, "text": text},
            )
            assert chunk.structured_content["accepted_chars"] == len(text)
        stored = await server.call_tool("commit_knowledge_artifact", {"artifact_id": artifact_id})
        assert stored.structured_content["artifact_id"] == artifact_id
        assert stored.structured_content["deduplicated"] is False
        page = await server.call_tool(
            "get_knowledge_artifact", {"artifact_id": artifact_id, "limit": 14}
        )
        assert page.structured_content["content"] == "tool,language\n"
        assert page.structured_content["next_offset"] == 14
        assert page.structured_content["untrusted_data"] is True
        with pytest.raises(ToolError, match="secret_detected: suspected"):
            rejected = await server.call_tool(
                "begin_knowledge_artifact",
                {
                    "idempotency_key": "mcp-artifact-secret",
                    "filename": "keys.txt",
                    "media_type": "text/plain",
                    "declared_chunks": 1,
                },
            )
            await server.call_tool(
                "append_knowledge_artifact",
                {
                    "artifact_id": rejected.structured_content["artifact_id"],
                    "chunk_number": 1,
                    "text": "key: sk-" + "abcdefghijklmnopqrstuvwxyz123456",
                },
            )
        with pytest.raises(ToolError, match="invalid_request: media_type"):
            await server.call_tool(
                "begin_knowledge_artifact",
                {
                    "idempotency_key": "mcp-artifact-binary",
                    "filename": "chart.png",
                    "media_type": "image/png",
                    "declared_chunks": 1,
                },
            )

        compatible = await server.call_tool("search", {"query": "multilingual MCP"})
        assert compatible.structured_content["results"][0]["id"] == assertion_id
        fetched = await server.call_tool("fetch", {"id": assertion_id})
        assert fetched.structured_content["metadata"]["untrusted_data"] is True
        detailed = await server.call_tool("search_knowledge", {"query": "multilingual", "limit": 5})
        assert detailed.structured_content["results"]
        got = await server.call_tool("get_knowledge", {"assertion_id": assertion_id})
        assert got.structured_content["id"] == assertion_id
        conflicts = await server.call_tool("list_knowledge_conflicts", {"limit": 5})
        assert conflicts.structured_content == {"conflicts": []}
        statistics = await server.call_tool("get_knowledge_statistics", {})
        assert statistics.structured_content["assertions_total"] == 1

        corrected = await server.call_tool(
            "correct_knowledge",
            {
                "idempotency_key": "mcp-correction",
                "correction": {
                    "content": "The user prefers multilingual MCP tools.",
                    "kind": "preference",
                    "origin": "user",
                    "topics": ["mcp"],
                    "supersedes_id": assertion_id,
                },
            },
        )
        correction_id = corrected.structured_content["assertion_ids"][0]
        with pytest.raises(ToolError, match="assertion_ids are required"):
            await server.call_tool("forget_knowledge", {"dry_run": True})
        with pytest.raises(ToolError, match="confirmation_token is required"):
            await server.call_tool("forget_knowledge", {"dry_run": False})
        preview = await server.call_tool(
            "forget_knowledge", {"dry_run": True, "assertion_ids": [correction_id]}
        )
        deleted = await server.call_tool(
            "forget_knowledge",
            {
                "dry_run": False,
                "confirmation_token": preview.structured_content["confirmation_token"],
            },
        )
        assert deleted.structured_content["deleted_count"] == 1

        abandoned = await server.call_tool(
            "begin_knowledge_flush",
            {"idempotency_key": "mcp-abort", "declared_parts": 1, "declared_items": 1},
        )
        aborted = await server.call_tool(
            "abort_knowledge_flush", {"batch_id": abandoned.structured_content["batch_id"]}
        )
        assert aborted.structured_content["aborted"] is True
    finally:
        auth_context_var.reset(context_token)
