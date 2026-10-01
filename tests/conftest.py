import asyncio
import contextlib
import hashlib
import hmac
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Any

import httpx2
import pytest
import uvicorn
from alembic import command
from alembic.config import Config
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer

from knowledge_vault.config import Settings
from knowledge_vault.container import Container, build_container
from knowledge_vault.embeddings.providers import DeterministicFakeProvider

# Tests must never inherit an operator's local `.env`; every setting is passed explicitly.
Settings.model_config["env_file"] = None

POSTGRES_IMAGE = (
    "pgvector/pgvector:0.8.1-pg17-trixie@"
    "sha256:137f044b0efe3d57f39b972b9b53641b1f2045b99d879e298bbf514a25787dcf"
)

PEPPER = "test-pepper-with-enough-entropy"
TOKENS = {
    "test-user": (
        "kv_test_token_0123456789abcdef0123456789",
        ["knowledge:read", "knowledge:write", "knowledge:admin"],
    ),
    "writer": ("kv_test_writer_0123456789abcdef012345678", ["knowledge:read", "knowledge:write"]),
    "reader": ("kv_test_reader_0123456789abcdef012345678", ["knowledge:read"]),
}


@pytest.fixture
def raw_token() -> str:
    return TOKENS["test-user"][0]


@pytest.fixture
def writer_token() -> str:
    return TOKENS["writer"][0]


@pytest.fixture
def reader_token() -> str:
    return TOKENS["reader"][0]


@pytest.fixture
def settings() -> Settings:
    # Match HMAC-SHA256 from the production token helper.
    records = json.dumps(
        [
            {
                "principal_id": principal,
                "sha256": hmac.new(PEPPER.encode(), token.encode(), hashlib.sha256).hexdigest(),
                "scopes": scopes,
            }
            for principal, (token, scopes) in TOKENS.items()
        ]
    )
    return Settings(
        environment="test",
        database_url="postgresql://unused/unused",
        token_pepper=PEPPER,
        bootstrap_tokens=records,
        public_base_url="https://vault.test",
        auth_issuer_url="https://issuer.test",
        embeddings_enabled=True,
        embedding_dimensions=384,
    )


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    os.environ["TESTCONTAINERS_RYUK_DISABLED"] = "true"
    with PostgresContainer(
        POSTGRES_IMAGE,
        username="knowledge_vault",
        password="integration-only-password",
        dbname="knowledge_vault",
        driver="psycopg",
    ) as postgres:
        url = postgres.get_connection_url(driver=None)
        os.environ["KNOWLEDGE_VAULT_ENVIRONMENT"] = "test"
        os.environ["KNOWLEDGE_VAULT_DATABASE_URL"] = url
        alembic = Config("alembic.ini")
        command.upgrade(alembic, "head")
        yield url


@pytest.fixture
async def integration_container(postgres_url: str, settings: Settings) -> AsyncIterator[Container]:
    integration_settings = settings.model_copy(update={"database_url": postgres_url})
    container = build_container(
        integration_settings, embedder=DeterministicFakeProvider(dimensions=384)
    )
    async with container.database.engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE confirmation_tokens, deletion_audit, knowledge_conflicts, "
                "embedding_jobs, flush_parts, flush_batches, assertion_sources, assertions "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield container
    await container.database.close()


class _InProcessServer(uvicorn.Server):
    """Uvicorn server that leaves the test runner's signal handlers alone."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


@pytest.fixture
async def serve() -> AsyncIterator[Callable[[Any], Awaitable[str]]]:
    """Start an ASGI app on a loopback port with its real lifespan and middleware stack."""
    running: list[tuple[_InProcessServer, asyncio.Task[None]]] = []

    async def start(app: Any) -> str:
        config = uvicorn.Config(
            app, host="127.0.0.1", port=0, log_config=None, access_log=False, proxy_headers=False
        )
        server = _InProcessServer(config)
        task = asyncio.create_task(server.serve())
        running.append((server, task))
        while not server.started:
            if task.done():
                task.result()
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    yield start
    for server, task in running:
        server.should_exit = True
        await task


class McpClient:
    """Minimal MCP client over Streamable HTTP for exercising the real transport."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> tuple[bool, Any]:
        """Return (is_error, structured content or error text)."""
        result = await self._session.call_tool(name, arguments or {})
        if result.is_error:
            return True, " ".join(getattr(item, "text", "") for item in result.content)
        return False, result.structured_content

    async def tools(self) -> dict[str, Any]:
        listing = await self._session.list_tools()
        return {tool.name: tool for tool in listing.tools}


@pytest.fixture
def mcp_connect() -> Callable[..., contextlib.AbstractAsyncContextManager[McpClient]]:
    @contextlib.asynccontextmanager
    async def connect(
        base_url: str, token: str | None = None, headers: dict[str, str] | None = None
    ) -> AsyncIterator[McpClient]:
        all_headers = dict(headers or {})
        if token:
            all_headers["Authorization"] = f"Bearer {token}"
        async with (
            httpx2.AsyncClient(headers=all_headers, timeout=30) as http,
            streamable_http_client(f"{base_url}/mcp", http_client=http) as streams,
            ClientSession(streams[0], streams[1]) as session,
        ):
            await session.initialize()
            yield McpClient(session)

    return connect
