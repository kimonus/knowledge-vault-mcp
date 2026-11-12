"""Seed or verify synthetic vault data over the disposable TLS MCP endpoint."""

import asyncio
import json
import os
import ssl
from pathlib import Path

import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

IDENTITY = {
    "subject": "router",
    "property": "endpoint",
    "scope": "recovery-ci",
    "state_type": "configuration",
}
CHUNKS = ("name,value\n", "router,fixture\n")


async def main() -> None:
    directory = Path(os.environ["KV_TEST_SECRETS_DIR"])
    token = (directory / "writer-token").read_text().strip()
    tls = ssl.create_default_context(cafile=str(directory / "tls-cert.pem"))
    state_file = Path(os.environ["KV_RECOVERY_STATE"])
    url = os.environ.get("KV_RECOVERY_URL", "https://localhost:8443/mcp")
    async with (
        httpx2.AsyncClient(
            verify=tls, headers={"Authorization": f"Bearer {token}"}, timeout=30, trust_env=False
        ) as http,
        streamable_http_client(url, http_client=http) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()

        async def call(name: str, arguments: dict) -> dict:
            result = await session.call_tool(name, arguments)
            assert not result.is_error, name
            assert result.structured_content is not None, name
            return result.structured_content

        if os.environ.get("KV_RECOVERY_VERIFY"):
            state = json.loads(await asyncio.to_thread(state_file.read_text))
        else:
            artifact = await call(
                "begin_knowledge_artifact",
                {
                    "idempotency_key": "recovery-artifact",
                    "filename": "fixture.csv",
                    "media_type": "text/csv",
                    "declared_chunks": 2,
                },
            )
            artifact_id = artifact["artifact_id"]
            for number, chunk in enumerate(CHUNKS, 1):
                await call(
                    "append_knowledge_artifact",
                    {"artifact_id": artifact_id, "chunk_number": number, "text": chunk},
                )
            artifact_id = (await call("commit_knowledge_artifact", {"artifact_id": artifact_id}))[
                "artifact_id"
            ]
            assertions = [
                {
                    "content": "The fixture describes a router.",
                    "kind": "artifact_observation",
                    "origin": "artifact",
                    "artifact_ids": [artifact_id],
                    "sources": [
                        {"url": "https://example.org/recovery", "title": "Synthetic source"}
                    ],
                }
            ]
            for day, value, mode in ((1, "alpha", "snapshot"), (31, "beta", "change")):
                assertions.append(
                    {
                        "content": f"The router endpoint is {value}.",
                        "kind": "configuration",
                        "origin": "user",
                        "evidence_state": "reported",
                        "observed_at": f"2026-01-{day:02}T00:00:00Z",
                        "provenance": [
                            {
                                "kind": "conversation",
                                "locator": f"fixture:{day}",
                                "conversation_id": str(day),
                                "message_ids": ["synthetic"],
                            }
                        ],
                        "temporal": {
                            "identity": IDENTITY,
                            "evidence_key": f"recovery:{day}",
                            "value": value,
                            "mode": mode,
                        },
                    }
                )
            batch = await call(
                "begin_knowledge_flush",
                {
                    "idempotency_key": "recovery-flush",
                    "declared_parts": 1,
                    "declared_items": len(assertions),
                },
            )
            batch_id = batch["batch_id"]
            appended = await call(
                "append_knowledge",
                {"batch_id": batch_id, "part_number": 1, "assertions": assertions},
            )
            assert appended["accepted"] == len(assertions)
            receipt = await call("commit_knowledge_flush", {"batch_id": batch_id})
            state = {"assertion": receipt["assertion_ids"][0], "artifact": artifact_id}
            await asyncio.to_thread(state_file.write_text, json.dumps(state))

        assertion = await call("get_knowledge", {"assertion_id": state["assertion"]})
        assert assertion["sources"][0]["title"] == "Synthetic source"
        assert any(item["id"] == state["artifact"] for item in assertion["artifacts"])
        page = await call(
            "get_knowledge_artifact", {"artifact_id": state["artifact"], "limit": 100}
        )
        assert page["content"] == "".join(CHUNKS) and page["untrusted_data"]
        current = await call("resolve_knowledge_state", {"request": {"identity": IDENTITY}})
        past = await call(
            "resolve_knowledge_state",
            {"request": {"identity": IDENTITY, "as_of": "2026-01-01T00:00:00Z"}},
        )
        assert current["values"] == ["beta"] and past["values"] == ["alpha"]
        assert past["history"][0]["assertion"]["provenance"][0]["message_ids"] == ["synthetic"]
    print("Synthetic vault fixture verified through authenticated TLS MCP")


if __name__ == "__main__":
    asyncio.run(main())
