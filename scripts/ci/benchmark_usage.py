"""Measure optional accounting overhead against a disposable 2,000-assertion corpus."""

import asyncio
import json
import os
import secrets
from hashlib import sha256
from time import perf_counter
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import insert
from testcontainers.community.postgres import PostgresContainer

from knowledge_vault.config import Settings
from knowledge_vault.container import build_container
from knowledge_vault.persistence.tables import AssertionRow
from knowledge_vault.services.search import SearchService

IMAGE = (
    "pgvector/pgvector:0.8.1-pg17-trixie@"
    "sha256:137f044b0efe3d57f39b972b9b53641b1f2045b99d879e298bbf514a25787dcf"
)


async def measure(url):
    settings = Settings(
        environment="test",
        database_url=url,
        embeddings_enabled=False,
        token_pepper=secrets.token_hex(32),
        _env_file=None,
    )
    c = build_container(settings)
    ids = []
    # This probe measures reads. Bulk-load synthetic rows instead of measuring ingestion's
    # independent conflict-detection work during fixture creation.
    async with c.database.sessions.begin() as session:
        for batch_number in range(4):
            values = []
            for index in range(500):
                ident = uuid4()
                ids.append(ident)
                content = f"Synthetic benchmark observation {batch_number}-{index}."
                values.append(
                    {
                        "id": ident,
                        "content": content,
                        "normalized_content": content.casefold(),
                        "content_hash": sha256(content.casefold().encode()).hexdigest(),
                        "kind": "artifact_observation",
                        "origin": "user",
                        "confidence": 1.0,
                        "topics": [],
                        "status": "current",
                        "sensitivity": "normal",
                        "embedding_state": "disabled",
                    }
                )
            await session.execute(insert(AssertionRow), values)
    output = {}
    # Threshold is fixed before measurements: p95 accounting overhead <= 100 ms on this host.
    for enabled in (False, True):
        search = SearchService(
            c.database.sessions,
            settings.model_copy(
                update={
                    "usage_tracking_enabled": enabled,
                }
            ),
            None,
        )
        for _ in range(5):
            await search.get(ids[0])
        semaphore = asyncio.Semaphore(10)

        async def read(index, sem=semaphore, service=search):
            async with sem:
                start = perf_counter()
                view = await service.get(ids[index % 5])
                assert view.usage.available
                return (perf_counter() - start) * 1000

        times = sorted(await asyncio.gather(*(read(i) for i in range(200))))
        output["enabled" if enabled else "disabled"] = {
            "p50_ms": round(times[99], 2),
            "p95_ms": round(times[189], 2),
            "p99_ms": round(times[197], 2),
        }
    await c.database.close()
    assert output["enabled"]["p95_ms"] - output["disabled"]["p95_ms"] <= 100
    print(json.dumps({"corpus": 2000, "reads_per_mode": 200, "concurrency": 10, **output}))


if __name__ == "__main__":
    os.environ["TESTCONTAINERS_RYUK_DISABLED"] = "true"
    Settings.model_config["env_file"] = None
    with PostgresContainer(
        IMAGE,
        username="benchmark",
        password=secrets.token_urlsafe(32),
        dbname="benchmark",
        driver="psycopg",
    ) as db:
        database_url = db.get_connection_url(driver=None)
        os.environ["KNOWLEDGE_VAULT_DATABASE_URL"] = database_url
        os.environ["KNOWLEDGE_VAULT_ENVIRONMENT"] = "test"
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(measure(database_url))
