"""Exercise a disposable standalone stack through its actual TLS gateway.

The caller supplies a CA certificate and test-only tokens in files. This client never
prints credentials or assertion contents. It requires the project's locked dependencies.
"""

import asyncio
import os
import ssl
from pathlib import Path

import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main() -> None:
    secrets = Path(os.environ["KV_TEST_SECRETS_DIR"])
    writer = (secrets / "writer-token").read_text().strip()
    reader = (secrets / "reader-token").read_text().strip()
    tls = ssl.create_default_context(cafile=str(secrets / "tls-cert.pem"))
    url = "https://localhost:8443"
    async with httpx2.AsyncClient(verify=tls, timeout=30, trust_env=False) as http:
        response = await http.post(f"{url}/api/v1/search", json={"query": "fixture"})
        assert response.status_code == 401, response.status_code
        response = await http.post(
            f"{url}/api/v1/search",
            headers={"Authorization": f"Bearer {writer}", "Host": "evil.example"},
            json={"query": "fixture"},
        )
        assert response.status_code == 400, response.status_code
        response = await http.post(
            f"{url}/mcp",
            headers={
                "Authorization": f"Bearer {writer}",
                "Origin": "https://evil.example",
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert response.status_code == 403, response.status_code
        response = await http.options(
            f"{url}/api/v1/search",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert "access-control-allow-origin" not in response.headers
        for path in ("/metrics", "/health/ready", "/api/openapi.json"):
            response = await http.get(url + path)
            assert response.status_code == 404, (path, response.status_code)

    async def call(session: ClientSession, name: str, arguments: dict) -> dict:
        result = await session.call_tool(name, arguments)
        assert not result.is_error, name
        assert result.structured_content is not None, name
        return result.structured_content

    headers = {"Authorization": f"Bearer {writer}"}
    async with (
        httpx2.AsyncClient(verify=tls, headers=headers, timeout=30, trust_env=False) as http,
        streamable_http_client(f"{url}/mcp", http_client=http) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()
        begun = await call(
            session,
            "begin_knowledge_flush",
            {"idempotency_key": "ci-disposable-flush", "declared_parts": 1, "declared_items": 1},
        )
        batch_id = begun["batch_id"]
        if os.environ.get("KV_TEST_READ_ONLY"):
            assert begun["replayed"]
            committed = await call(session, "commit_knowledge_flush", {"batch_id": batch_id})
            assertion_id = committed["assertion_ids"][0]
            fetched = await call(session, "get_knowledge", {"assertion_id": assertion_id})
            assert fetched["id"] == assertion_id
            print("Persisted assertion survived the stack restart")
            return
        appended = await call(
            session,
            "append_knowledge",
            {
                "batch_id": batch_id,
                "part_number": 1,
                "assertions": [
                    {
                        "content": "A disposable CI fixture uses a keyboard.",
                        "kind": "configuration",
                        "origin": "user",
                        "topics": ["keyboard"],
                    }
                ],
            },
        )
        assert appended["accepted"] == 1
        committed = await call(session, "commit_knowledge_flush", {"batch_id": batch_id})
        assert committed["counts"]["inserted"] == 1
        assertion_id = committed["assertion_ids"][0]
        fetched = await call(session, "get_knowledge", {"assertion_id": assertion_id})
        assert fetched["id"] == assertion_id

    async with (
        httpx2.AsyncClient(
            verify=tls, headers={"Authorization": f"Bearer {reader}"}, timeout=30, trust_env=False
        ) as http,
        streamable_http_client(f"{url}/mcp", http_client=http) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()
        denied = await session.call_tool(
            "begin_knowledge_flush",
            {"idempotency_key": "ci-denied", "declared_parts": 1, "declared_items": 1},
        )
        assert denied.is_error
    print("Authenticated TLS gateway and MCP checks passed")


if __name__ == "__main__":
    asyncio.run(main())
