import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from knowledge_vault.api.app import create_app
from knowledge_vault.domain.enums import ArtifactState
from knowledge_vault.persistence.tables import ArtifactChunkRow, ArtifactRow
from knowledge_vault.services.errors import (
    BatchIncompleteError,
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    SecretDetectedError,
)

PRINCIPAL = "test-user"
# Assembled at run time so that no secret-shaped literal sits in the repository.
SECRET = "sk-" + "abcdefghijklmnopqrstuvwxyz123456"


async def upload(container, key: str, chunks: list[str], *, filename: str = "data.csv"):
    artifacts = container.artifacts
    artifact = await artifacts.begin(PRINCIPAL, key, filename, "text/csv", len(chunks))
    for number, text in enumerate(chunks, start=1):
        await artifacts.append(PRINCIPAL, artifact.id, number, text)
    return await artifacts.commit(PRINCIPAL, artifact.id)


async def flush(container, key: str, items: list[dict]) -> list:
    ingestion = container.ingestion
    batch = await ingestion.begin(PRINCIPAL, key, 1, len(items))
    await ingestion.append(PRINCIPAL, batch.id, 1, items)
    return (await ingestion.commit(PRINCIPAL, batch.id)).assertion_ids


def assertion(content: str, artifact_ids: list) -> dict:
    return {
        "content": content,
        "kind": "artifact_observation",
        "origin": "artifact",
        "artifact_ids": [str(item) for item in artifact_ids],
    }


async def count(container, model, *where) -> int:
    async with container.database.sessions() as session:
        return await session.scalar(select(func.count()).select_from(model).where(*where)) or 0


@pytest.mark.integration
async def test_upload_is_idempotent_ordered_and_bounded(integration_container) -> None:
    container = integration_container
    artifacts = container.artifacts

    artifact = await artifacts.begin(PRINCIPAL, "upload-1", "plan.md", "Text/Markdown", 3, "A plan")
    assert artifact.media_type == "text/markdown"
    replay = await artifacts.begin(PRINCIPAL, "upload-1", "plan.md", "text/markdown", 3, "A plan")
    assert replay.id == artifact.id and replay.replayed
    with pytest.raises(ConflictError, match="another artifact"):
        await artifacts.begin(PRINCIPAL, "upload-1", "other.md", "text/markdown", 3, "A plan")
    with pytest.raises(InvalidRequestError, match="media_type"):
        await artifacts.begin(PRINCIPAL, "upload-2", "chart.png", "image/png", 1)
    with pytest.raises(InvalidRequestError, match="path separators"):
        await artifacts.begin(PRINCIPAL, "upload-2", "../plan.md", "text/markdown", 1)
    with pytest.raises(InvalidRequestError, match="declared_chunks"):
        await artifacts.begin(PRINCIPAL, "upload-2", "plan.md", "text/markdown", 10_000)
    with pytest.raises(SecretDetectedError):
        await artifacts.begin(PRINCIPAL, "upload-2", "plan.md", "text/markdown", 1, SECRET)

    # Chunks may arrive in any order and are joined by number.
    assert await artifacts.append(PRINCIPAL, artifact.id, 3, "third\n") == (6, False)
    assert await artifacts.append(PRINCIPAL, artifact.id, 1, "first\n") == (6, False)
    assert await artifacts.append(PRINCIPAL, artifact.id, 1, "first\n") == (6, True)
    with pytest.raises(ConflictError, match="different payload"):
        await artifacts.append(PRINCIPAL, artifact.id, 1, "changed\n")
    with pytest.raises(ConflictError, match="outside the declared range"):
        await artifacts.append(PRINCIPAL, artifact.id, 4, "fourth\n")
    with pytest.raises(InvalidRequestError, match="1 through"):
        await artifacts.append(PRINCIPAL, artifact.id, 2, "")
    with pytest.raises(InvalidRequestError, match="NUL"):
        await artifacts.append(PRINCIPAL, artifact.id, 2, "a\x00b")
    with pytest.raises(NotFoundError):
        await artifacts.append("someone-else", artifact.id, 2, "second\n")
    with pytest.raises(BatchIncompleteError, match=r"missing chunks=\[2\]"):
        await artifacts.commit(PRINCIPAL, artifact.id)

    await artifacts.append(PRINCIPAL, artifact.id, 2, "żółć second\n")
    stored = await artifacts.commit(PRINCIPAL, artifact.id)
    content = "first\nżółć second\nthird\n"
    assert stored.artifact_id == artifact.id
    assert stored.size_bytes == len(content.encode())
    assert (stored.deduplicated, stored.replayed) == (False, False)
    again = await artifacts.commit(PRINCIPAL, artifact.id)
    assert (again.artifact_id, again.sha256, again.replayed) == (artifact.id, stored.sha256, True)
    with pytest.raises(ConflictError, match="stored state"):
        await artifacts.append(PRINCIPAL, artifact.id, 1, "first\n")
    # Staged text is not kept once the artifact is stored.
    assert await count(container, ArtifactChunkRow) == 0

    first = await artifacts.read(artifact.id, limit=10)
    assert (first.content, first.total_chars, first.next_offset) == (content[:10], len(content), 10)
    rest = await artifacts.read(artifact.id, offset=10, limit=8000)
    assert first.content + rest.content == content and rest.next_offset is None
    assert (await artifacts.read(artifact.id, offset=500)).content == ""
    with pytest.raises(InvalidRequestError, match="limit"):
        await artifacts.read(artifact.id, limit=10**6)
    with pytest.raises(NotFoundError):
        await artifacts.read(uuid4())


@pytest.mark.integration
async def test_identical_content_is_stored_once(integration_container) -> None:
    container = integration_container
    first = await upload(container, "copy-1", ["a,b\n1,2\n"])
    second = await upload(container, "copy-2", ["a,b\n", "1,2\n"], filename="again.csv")
    assert second.artifact_id == first.artifact_id
    assert (second.deduplicated, second.filename) == (True, "data.csv")
    assert await count(container, ArtifactRow, ArtifactRow.state == ArtifactState.STORED) == 1

    # Replaying the second upload still resolves to the stored artifact.
    replay = await container.artifacts.begin(PRINCIPAL, "copy-2", "again.csv", "text/csv", 2)
    resolved = await container.artifacts.commit(PRINCIPAL, replay.id)
    assert (resolved.artifact_id, resolved.deduplicated) == (first.artifact_id, True)
    assert (await container.artifacts.read(replay.id)).artifact_id == first.artifact_id


@pytest.mark.integration
@pytest.mark.parametrize(
    "chunks",
    [
        ["token = " + SECRET],
        ["notes\n", "token = " + SECRET, "\nmore"],
        # Split across a chunk boundary, in both arrival orders.
        ["token = " + SECRET[:12], SECRET[12:] + "\n"],
    ],
)
async def test_a_secret_discards_the_whole_artifact(integration_container, chunks) -> None:
    container = integration_container
    for order in (range(1, len(chunks) + 1), range(len(chunks), 0, -1)):
        artifact = await container.artifacts.begin(
            PRINCIPAL, f"secret-{len(chunks)}-{order[0]}", "notes.txt", "text/plain", len(chunks)
        )
        with pytest.raises(SecretDetectedError, match="suspected OpenAI API key"):
            for number in order:
                await container.artifacts.append(PRINCIPAL, artifact.id, number, chunks[number - 1])
        # Neither the artifact nor any staged chunk survives.
        assert await count(container, ArtifactRow) == 0
        assert await count(container, ArtifactChunkRow) == 0


@pytest.mark.integration
async def test_assertions_reference_artifacts_and_forgetting_removes_them(
    integration_container,
) -> None:
    container = integration_container
    shared = await upload(container, "shared", ["region,total\nPL,10\n"])
    only = await upload(container, "only", ["print('hello')\n"], filename="hello.py")

    with pytest.raises(NotFoundError, match="commit the artifact before the flush"):
        await flush(container, "bad-link", [assertion("Refers to nothing.", [uuid4()])])

    first_id, second_id = await flush(
        container,
        "linked",
        [
            assertion("The totals table lists sales by region.", [shared.artifact_id]),
            assertion(
                "The hello script prints a greeting.", [only.artifact_id, shared.artifact_id]
            ),
        ],
    )
    view = await container.search.get(second_id)
    assert {item.filename for item in view.artifacts} == {"data.csv", "hello.py"}
    assert all(item.size_bytes > 0 for item in view.artifacts)

    # Submitting known wording again adds the new link instead of duplicating the assertion.
    extra = await upload(container, "extra", ["x\n"], filename="x.txt")
    (confirmed_id,) = await flush(
        container,
        "relinked",
        [assertion("The totals table lists sales by region.", [extra.artifact_id])],
    )
    assert confirmed_id == first_id
    assert len((await container.search.get(first_id)).artifacts) == 2
    assert (await container.administration.statistics())["artifacts_total"] == 3

    admin = container.administration
    preview = await admin.preview_forgetting(PRINCIPAL, [second_id])
    # `shared` is still described by the first assertion; only `hello.py` would go.
    assert preview["artifact_count"] == 1
    result = await admin.confirm_forgetting(PRINCIPAL, preview["confirmation_token"])
    assert (result["deleted_count"], result["deleted_artifact_count"]) == (1, 1)
    with pytest.raises(NotFoundError):
        await container.artifacts.read(only.artifact_id)
    assert (await container.artifacts.read(shared.artifact_id)).content.startswith("region")

    preview = await admin.preview_forgetting(PRINCIPAL, [first_id])
    assert preview["artifact_count"] == 2
    result = await admin.confirm_forgetting(PRINCIPAL, preview["confirmation_token"])
    assert result["deleted_artifact_count"] == 2
    assert await count(container, ArtifactRow) == 0


@pytest.mark.integration
async def test_purge_removes_abandoned_uploads_and_unreferenced_artifacts(
    integration_container,
) -> None:
    container = integration_container
    kept = await upload(container, "kept", ["kept\n"])
    await flush(container, "keeps", [assertion("A kept note exists.", [kept.artifact_id])])
    unreferenced = await upload(container, "unreferenced", ["nobody describes this\n"])
    recent = await upload(container, "recent", ["just stored\n"])
    abandoned = await container.artifacts.begin(PRINCIPAL, "abandoned", "a.txt", "text/plain", 2)
    await container.artifacts.append(PRINCIPAL, abandoned.id, 1, "half")

    long_ago = datetime.now(UTC) - timedelta(days=30)
    async with container.database.sessions.begin() as session:
        await session.execute(
            update(ArtifactRow)
            .where(ArtifactRow.id.in_([kept.artifact_id, unreferenced.artifact_id]))
            .values(stored_at=long_ago)
        )
        await session.execute(
            update(ArtifactRow).where(ArtifactRow.id == abandoned.id).values(expires_at=long_ago)
        )

    assert await container.artifacts.purge() == (1, 1)
    assert await container.artifacts.purge() == (0, 0)
    remaining = {kept.artifact_id, recent.artifact_id}
    async with container.database.sessions() as session:
        assert set((await session.scalars(select(ArtifactRow.id))).all()) == remaining
    assert await count(container, ArtifactChunkRow) == 0

    # An upload that outlives its window is refused and discarded when it is next touched.
    late = await container.artifacts.begin(PRINCIPAL, "late", "late.txt", "text/plain", 1)
    async with container.database.sessions.begin() as session:
        await session.execute(
            update(ArtifactRow).where(ArtifactRow.id == late.id).values(expires_at=long_ago)
        )
    with pytest.raises(ConflictError, match="expired"):
        await container.artifacts.append(PRINCIPAL, late.id, 1, "too late")
    with pytest.raises(NotFoundError):
        await container.artifacts.commit(PRINCIPAL, late.id)


@pytest.mark.integration
async def test_commit_rechecks_the_assembled_text_and_the_window(integration_container) -> None:
    container = integration_container
    artifacts = container.artifacts
    with pytest.raises(InvalidRequestError, match="idempotency_key"):
        await artifacts.begin(PRINCIPAL, "", "a.txt", "text/plain", 1)
    with pytest.raises(InvalidRequestError, match="offset"):
        await artifacts.read(uuid4(), offset=-1)

    # A staged chunk that slipped past the per-chunk check is still caught on commit.
    artifact = await artifacts.begin(PRINCIPAL, "late-secret", "a.txt", "text/plain", 1)
    async with container.database.sessions.begin() as session:
        session.add(
            ArtifactChunkRow(
                artifact_id=artifact.id, chunk_number=1, payload_hash="0" * 64, content=SECRET
            )
        )
    with pytest.raises(SecretDetectedError, match="the artifact was discarded"):
        await artifacts.commit(PRINCIPAL, artifact.id)
    assert await count(container, ArtifactRow) == 0

    expired = await artifacts.begin(PRINCIPAL, "expired-commit", "a.txt", "text/plain", 1)
    await artifacts.append(PRINCIPAL, expired.id, 1, "text")
    async with container.database.sessions.begin() as session:
        await session.execute(
            update(ArtifactRow)
            .where(ArtifactRow.id == expired.id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    with pytest.raises(ConflictError, match="expired"):
        await artifacts.commit(PRINCIPAL, expired.id)
    assert await count(container, ArtifactRow) == 0


@pytest.mark.integration
async def test_concurrent_uploads_of_the_same_content_converge(integration_container) -> None:
    container = integration_container
    artifacts = container.artifacts
    pending = []
    for number in range(4):
        artifact = await artifacts.begin(PRINCIPAL, f"race-{number}", "r.txt", "text/plain", 1)
        await artifacts.append(PRINCIPAL, artifact.id, 1, "identical content\n")
        pending.append(artifact.id)
    results = await asyncio.gather(*(artifacts.commit(PRINCIPAL, item) for item in pending))
    assert len({result.artifact_id for result in results}) == 1
    assert sum(not result.deduplicated for result in results) == 1
    assert await count(container, ArtifactRow, ArtifactRow.state == ArtifactState.STORED) == 1


@pytest.mark.integration
async def test_commit_retries_only_the_duplicate_content_race(
    integration_container, monkeypatch
) -> None:
    container = integration_container
    artifacts = container.artifacts
    artifact = await artifacts.begin(PRINCIPAL, "raced", "r.txt", "text/plain", 1)
    await artifacts.append(PRINCIPAL, artifact.id, 1, "raced content\n")

    def violation(constraint: str) -> IntegrityError:
        cause = Exception("unique violation")
        cause.diag = SimpleNamespace(constraint_name=constraint)  # type: ignore[attr-defined]
        return IntegrityError("INSERT", {}, cause)

    real = artifacts._commit_once
    failures = [violation("uq_artifacts_stored_content")]

    async def lose_once(principal_id, artifact_id):
        if failures:
            raise failures.pop()
        return await real(principal_id, artifact_id)

    monkeypatch.setattr(artifacts, "_commit_once", lose_once)
    assert (await artifacts.commit(PRINCIPAL, artifact.id)).artifact_id == artifact.id

    # Any other violation is deterministic and must surface instead of being retried.
    failures.append(violation("ck_artifact_state"))
    with pytest.raises(IntegrityError):
        await artifacts.commit(PRINCIPAL, artifact.id)

    async def always_lose(principal_id, artifact_id):
        raise violation("uq_artifacts_stored_content")

    monkeypatch.setattr(artifacts, "_commit_once", always_lose)
    with pytest.raises(ConflictError, match="retry the same commit"):
        await artifacts.commit(PRINCIPAL, artifact.id)


@pytest.mark.integration
async def test_oversized_artifact_is_refused(integration_container) -> None:
    container = integration_container
    container.artifacts._settings = container.settings.model_copy(
        update={"artifact_max_bytes": 1024}
    )
    artifact = await container.artifacts.begin(PRINCIPAL, "big", "big.txt", "text/plain", 1)
    await container.artifacts.append(PRINCIPAL, artifact.id, 1, "x" * 2000)
    with pytest.raises(InvalidRequestError, match="exceeds 1024 bytes"):
        await container.artifacts.commit(PRINCIPAL, artifact.id)


@pytest.mark.integration
@pytest.mark.e2e
async def test_http_artifact_routes(
    integration_container, raw_token: str, reader_token: str
) -> None:
    app = create_app(integration_container)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    headers = {"Authorization": f"Bearer {raw_token}"}
    async with AsyncClient(transport=transport, base_url="https://vault.test") as client:
        body = {
            "idempotency_key": "http-artifact",
            "filename": "report.json",
            "media_type": "application/json",
            "declared_chunks": 1,
        }
        assert (await client.post("/api/v1/artifacts", json=body)).status_code == 401
        denied = await client.post(
            "/api/v1/artifacts", json=body, headers={"Authorization": f"Bearer {reader_token}"}
        )
        assert denied.status_code == 403
        begin = await client.post("/api/v1/artifacts", json=body, headers=headers)
        assert begin.status_code == 201
        artifact_id = begin.json()["artifact_id"]
        chunk = await client.put(
            f"/api/v1/artifacts/{artifact_id}/chunks/1",
            json={"text": '{"ok": true}'},
            headers=headers,
        )
        assert chunk.json() == {
            "artifact_id": artifact_id,
            "chunk_number": 1,
            "accepted_chars": 12,
            "replayed": False,
        }
        commit = await client.post(f"/api/v1/artifacts/{artifact_id}/commit", headers=headers)
        assert commit.status_code == 200 and commit.json()["size_bytes"] == 12
        page = await client.get(f"/api/v1/artifacts/{artifact_id}?limit=5", headers=headers)
        assert page.json()["content"] == '{"ok"' and page.json()["untrusted_data"] is True

        secret = await client.post(
            "/api/v1/artifacts", json={**body, "idempotency_key": "http-secret"}, headers=headers
        )
        rejected = await client.put(
            f"/api/v1/artifacts/{secret.json()['artifact_id']}/chunks/1",
            json={"text": "password " + SECRET},
            headers=headers,
        )
        assert rejected.status_code == 422
        assert rejected.json()["type"].endswith(":secret_detected")
        assert SECRET not in rejected.text
