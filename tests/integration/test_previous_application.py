"""Run the preceding published application against the expanded schema (real rollback)."""

import hashlib
import hmac
import json
import secrets
import time
from uuid import uuid4

import docker
import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from testcontainers.community.postgres import PostgresContainer

from conftest import POSTGRES_IMAGE

PREVIOUS_IMAGE = (
    "ghcr.io/kimonus/knowledge-vault-mcp@"
    "sha256:17059fb69eae93fa2cb4550edf3dde56cf6c8ecafbaf2ca3c66be57b70460ea7"
)


@pytest.mark.integration
def test_previous_application_on_expanded_schema(monkeypatch):
    client = docker.from_env()
    network = client.networks.create("kv-rollback-" + uuid4().hex[:12])
    password, token, pepper = (secrets.token_urlsafe(32) for _ in range(3))
    api = None
    try:
        with (
            PostgresContainer(
                POSTGRES_IMAGE,
                username="rollback",
                password=password,
                dbname="rollback",
                driver="psycopg",
            )
            .with_network(network)
            .with_network_aliases("database") as db
        ):
            url = db.get_connection_url(driver=None)
            monkeypatch.setenv("KNOWLEDGE_VAULT_DATABASE_URL", url)
            monkeypatch.setenv("KNOWLEDGE_VAULT_ENVIRONMENT", "test")
            command.upgrade(Config("alembic.ini"), "head")
            try:
                client.images.get(PREVIOUS_IMAGE)
            except docker.errors.ImageNotFound:
                client.images.pull(PREVIOUS_IMAGE)
            api = client.containers.run(
                PREVIOUS_IMAGE,
                detach=True,
                network=network.name,
                ports={"8000/tcp": None},
                read_only=True,
                tmpfs={"/tmp": "rw,size=64m"},  # noqa: S108 - private container tmpfs
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                environment={
                    "KNOWLEDGE_VAULT_ENVIRONMENT": "test",
                    "KNOWLEDGE_VAULT_DATABASE_URL": f"postgresql+psycopg://rollback:{password}@database:5432/rollback",
                    "KNOWLEDGE_VAULT_TOKEN_PEPPER": pepper,
                    "KNOWLEDGE_VAULT_BOOTSTRAP_TOKENS": json.dumps(
                        [
                            {
                                "principal_id": "rollback-probe",
                                "sha256": hmac.new(
                                    pepper.encode(), token.encode(), hashlib.sha256
                                ).hexdigest(),
                                "scopes": ["knowledge:read", "knowledge:write"],
                            }
                        ]
                    ),
                    "KNOWLEDGE_VAULT_EMBEDDINGS_ENABLED": "false",
                    "KNOWLEDGE_VAULT_PUBLIC_BASE_URL": "https://rollback.test",
                    "KNOWLEDGE_VAULT_AUTH_ISSUER_URL": "https://rollback.test",
                },
            )
            api.reload()
            port = api.attrs["NetworkSettings"]["Ports"]["8000/tcp"][0]["HostPort"]
            with httpx.Client(
                base_url="http://127.0.0.1:" + port,
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
            ) as http:
                for _attempt in range(100):
                    try:
                        if http.get("/health/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.2)
                else:
                    pytest.fail("previous release did not become ready on expanded schema")
                body = {
                    "idempotency_key": "legacy-binary-probe",
                    "declared_parts": 1,
                    "declared_items": 1,
                }
                begun = http.post("/api/v1/flushes", json=body)
                assert begun.status_code == 201
                ident = begun.json()["batch_id"]
                appended = http.put(
                    f"/api/v1/flushes/{ident}/parts/1",
                    json={
                        "assertions": [
                            {
                                "content": "Synthetic observation written by the previous release.",
                                "kind": "artifact_observation",
                                "origin": "user",
                            }
                        ]
                    },
                )
                assert appended.status_code == 200
                committed = http.post(f"/api/v1/flushes/{ident}/commit")
                assert committed.status_code == 200
                assertion_id = committed.json()["assertion_ids"][0]
                assert http.get(f"/api/v1/assertions/{assertion_id}").status_code == 200
                assert (
                    http.post("/api/v1/search", json={"query": "previous release"}).status_code
                    == 200
                )
            engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://"))
            try:
                with engine.connect() as connection:
                    assert connection.execute(
                        text("SELECT evidence_state,revision FROM assertions WHERE id=:id"),
                        {"id": assertion_id},
                    ).one() == ("unknown", 1)
            finally:
                engine.dispose()
            assert token not in api.logs().decode()
    finally:
        if api:
            api.remove(force=True)
        network.remove()
        client.close()
