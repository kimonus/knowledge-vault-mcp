"""Real disposable pgvector/core/BFF browser proof; no operator .env or live data."""

import asyncio
import json
import os
import secrets
import signal
import sys
import tempfile
from uuid import UUID

import httpx
import uvicorn
from alembic import command
from alembic.config import Config
from server import CREDENTIAL, FIXTURE, REPO, SERVICE, Fixture, TestServer, certificate
from testcontainers.community.postgres import PostgresContainer

sys.path.insert(0, str(REPO / "src"))
from knowledge_vault.api.app import create_app
from knowledge_vault.auth.tokens import hash_token
from knowledge_vault.config import Settings
from knowledge_vault.container import build_container
from knowledge_vault.persistence.tables import (
    AssertionLinkRow,
    KnowledgeContextRow,
    SourceRow,
)

Settings.model_config["env_file"] = None
POSTGRES = (
    "pgvector/pgvector:0.8.1-pg17-trixie@"
    "sha256:137f044b0efe3d57f39b972b9b53641b1f2045b99d879e298bbf514a25787dcf"
)


async def seed(container):
    async with container.database.sessions.begin() as session:
        session.add(
            KnowledgeContextRow(
                id=UUID(FIXTURE["context"]["id"]),
                name=FIXTURE["context"]["name"],
                aliases=FIXTURE["context"]["aliases"],
            )
        )
    artifact = await container.artifacts.begin(
        "synthetic-setup", "browser-artifact", "evidence.txt", "text/plain", 1
    )
    await container.artifacts.append(
        "synthetic-setup", artifact.id, 1, FIXTURE["artifact"]["content"]
    )
    artifact = await container.artifacts.commit("synthetic-setup", artifact.id)
    assertions = []
    for index, detail in enumerate(FIXTURE["details"]):
        assertion = {
            key: detail[key]
            for key in [
                "content",
                "kind",
                "origin",
                "status",
                "confidence",
                "topics",
                "sensitivity",
                "context_id",
                "facet",
                "evidence_state",
                "temporal_role",
                "workflow_state",
                "last_verified_at",
                "provenance",
                "observed_at",
            ]
        }
        assertion["sources"] = [
            {"url": "https://example.test/real-source", "title": "Actual core synthetic source"}
        ]
        if index == 0:
            assertion["artifact_ids"] = [str(artifact.artifact_id)]
            assertion["temporal"] = FIXTURE["evidence"]["assertion"]["temporal"]
        assertions.append(assertion)
    batch = await container.ingestion.begin(
        "synthetic-setup", "browser-fixtures", 1, len(assertions)
    )
    await container.ingestion.append("synthetic-setup", batch.id, 1, assertions)
    result = await container.ingestion.commit("synthetic-setup", batch.id)
    if result.counts.inserted != len(assertions):
        raise RuntimeError("Synthetic browser fixture setup rejected an assertion")
    async with container.database.sessions.begin() as session:
        session.add_all(
            [
                SourceRow(
                    assertion_id=result.assertion_ids[0],
                    url=f"https://example.test/real-{n}",
                    title=f"Actual core source {n}",
                )
                for n in range(42)
            ]
        )
        session.add(
            AssertionLinkRow(
                source_id=result.assertion_ids[0],
                target_id=result.assertion_ids[2],
                kind="depends_on",
            )
        )


async def serve(url, records, pepper):
    settings = Settings(
        environment="test",
        database_url=url,
        token_pepper=pepper,
        bootstrap_tokens=records,
        inspection_principals="browser-inspection",
        public_base_url="https://core-private.test",
        auth_issuer_url="https://core-private.test",
        embeddings_enabled=False,
        usage_tracking_enabled=True,
    )
    container = build_container(settings)
    await seed(container)
    fixture = Fixture(transport=httpx.ASGITransport(create_app(container)), port=9445)
    stop = asyncio.Event()
    for event in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(event, stop.set)
    try:
        with tempfile.TemporaryDirectory(prefix="kv-real-browser-tls-") as directory:
            cert, key = certificate(directory)
            server = TestServer(
                uvicorn.Config(
                    fixture,
                    host="127.0.0.1",
                    port=9445,
                    access_log=False,
                    log_level="warning",
                    proxy_headers=False,
                    ssl_certfile=cert,
                    ssl_keyfile=key,
                )
            )
            task = asyncio.create_task(server.serve())
            await stop.wait()
            server.should_exit = True
            await task
    finally:
        await container.database.close()


if __name__ == "__main__":
    assert SERVICE != CREDENTIAL
    pepper = secrets.token_urlsafe(36)
    records = json.dumps(
        [
            {
                "principal_id": "browser-inspection",
                "sha256": hash_token(SERVICE, pepper).hex(),
                "scopes": ["knowledge:read"],
            }
        ]
    )
    os.environ["TESTCONTAINERS_RYUK_DISABLED"] = "true"
    with PostgresContainer(
        POSTGRES,
        username="knowledge_vault",
        password=secrets.token_urlsafe(24),
        dbname="browser",
        driver="psycopg",
    ) as postgres:
        url = postgres.get_connection_url(driver=None)
        os.environ.update(KNOWLEDGE_VAULT_ENVIRONMENT="test", KNOWLEDGE_VAULT_DATABASE_URL=url)
        config = Config(str(REPO / "alembic.ini"))
        config.set_main_option("script_location", str(REPO / "migrations"))
        command.upgrade(config, "head")
        asyncio.run(serve(url, records, pepper))
