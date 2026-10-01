import hashlib
import json
import os
from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer

from knowledge_vault.config import Settings
from knowledge_vault.container import Container, build_container
from knowledge_vault.embeddings.providers import DeterministicFakeProvider

POSTGRES_IMAGE = (
    "pgvector/pgvector:0.8.1-pg17-trixie@"
    "sha256:137f044b0efe3d57f39b972b9b53641b1f2045b99d879e298bbf514a25787dcf"
)


@pytest.fixture
def raw_token() -> str:
    return "kv_test_token_0123456789abcdef0123456789"


@pytest.fixture
def settings(raw_token: str) -> Settings:
    pepper = "test-pepper-with-enough-entropy"
    # Match HMAC-SHA256 from the production token helper.
    import hmac

    token_hash = hmac.new(pepper.encode(), raw_token.encode(), hashlib.sha256).hexdigest()
    records = json.dumps(
        [
            {
                "principal_id": "test-user",
                "sha256": token_hash,
                "scopes": ["knowledge:read", "knowledge:write", "knowledge:admin"],
            }
        ]
    )
    return Settings(
        environment="test",
        database_url="postgresql://unused/unused",
        token_pepper=pepper,
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
