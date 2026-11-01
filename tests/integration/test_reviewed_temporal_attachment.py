"""Preservation and historical qualification against disposable PostgreSQL."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import httpx2
import pytest
from sqlalchemy import func, select, text

from knowledge_vault.api.app import create_app
from knowledge_vault.domain.maintenance import ContextInput, MetadataUpdate
from knowledge_vault.domain.models import ReviewedTemporalAttachment
from knowledge_vault.domain.temporal import ResolutionRequest
from knowledge_vault.persistence.tables import AssertionRow, TemporalEvidenceRow
from knowledge_vault.services.errors import ConflictError, InvalidRequestError
from knowledge_vault.services.maintenance import maintenance_lock

pytestmark = pytest.mark.integration


def identity(scope: str) -> dict[str, str]:
    return {
        "subject": "router",
        "property": "wireguard.endpoint",
        "scope": scope,
        "state_type": "configuration",
    }


async def ingest(container, content: str, **fields):
    batch = await container.ingestion.begin("test-user", str(uuid4()), 1, 1)
    accepted, rejected, _ = await container.ingestion.append(
        "test-user",
        batch.id,
        1,
        [{"content": content, "kind": "configuration", "origin": "user", **fields}],
    )
    assert accepted == 1 and not rejected
    return (await container.ingestion.commit("test-user", batch.id)).assertion_ids[0]


async def fingerprint(container, assertion_id):
    async with container.database.sessions() as session:
        row = await session.scalar(
            text("SELECT to_jsonb(a) FROM assertions a WHERE id=:id"), {"id": assertion_id}
        )
        sources = (
            (
                await session.execute(
                    text(
                        "SELECT to_jsonb(s) FROM assertion_sources s "
                        "WHERE assertion_id=:id ORDER BY id"
                    ),
                    {"id": assertion_id},
                )
            )
            .scalars()
            .all()
        )
        successors = (
            (
                await session.execute(
                    text("SELECT id FROM assertions WHERE supersedes_id=:id ORDER BY id"),
                    {"id": assertion_id},
                )
            )
            .scalars()
            .all()
        )
    return row, sources, successors


def attachment(
    assertion_id,
    content_hash,
    content,
    status,
    scope,
    *,
    key,
    value,
    mode,
    observed="2026-02-01T00:00:00Z",
    valid_from=None,
    valid_to=None,
    transition_assertion_id=None,
    reason="unknown",
):
    return ReviewedTemporalAttachment.model_validate(
        {
            "assertion_id": assertion_id,
            "expected_content_hash": content_hash,
            "evidence": {
                "content": content,
                "kind": "configuration",
                "origin": "user",
                "status": status,
                "evidence_state": "verified",
                "last_verified_at": "2026-03-01T00:00:00Z",
                "observed_at": observed,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "sources": [{"url": "https://example.org/router-review"}],
                "temporal": {
                    "identity": identity(scope),
                    "evidence_key": key,
                    "value": value,
                    "mode": mode,
                    "observation_basis": "source",
                    "legacy_supersession_reason": reason,
                    "transition_assertion_id": transition_assertion_id,
                },
            },
        }
    )


async def resolve(container, scope, **kwargs):
    return await container.temporal.resolve(ResolutionRequest(identity=identity(scope), **kwargs))


async def test_reviewed_attachment_preserves_legacy_and_resolves_both_import_orders(
    integration_container,
):
    c = integration_container
    projections = []
    for scope, order in (("forward", ("old", "new")), ("reverse", ("new", "old"))):
        old_content = f"Router X was the WireGuard endpoint in {scope}."
        new_content = f"Router B is the WireGuard endpoint in {scope}."
        old = await ingest(c, old_content, sources=[{"url": "https://example.org/legacy"}])
        new = await ingest(c, new_content, supersedes_id=str(old))
        async with c.database.sessions.begin() as session:
            await session.execute(
                text(
                    "UPDATE assertions SET embedding=CAST(:vector AS vector(384)), "
                    "embedding_state='ready', embedding_model='synthetic-model' WHERE id=:id"
                ),
                {"vector": "[" + ",".join(["0"] * 384) + "]", "id": old},
            )
        before = await fingerprint(c, old)
        assert before[0]["embedding"] is not None
        old_hash = before[0]["content_hash"]
        new_hash = (await fingerprint(c, new))[0]["content_hash"]
        old_request = attachment(
            old,
            old_hash,
            old_content,
            "superseded",
            scope,
            key=f"{scope}:old",
            value="Router X",
            mode="historical",
            valid_from="2025-01-01T00:00:00Z",
            valid_to="2026-01-01T00:00:00Z",
            transition_assertion_id=new,
            reason="historical_transition",
        )
        new_request = attachment(
            new,
            new_hash,
            new_content,
            "current",
            scope,
            key=f"{scope}:new",
            value="Router B",
            mode="change",
            observed="2026-01-01T00:00:00Z",
            valid_from="2026-01-01T00:00:00Z",
        )
        before_current = await resolve(c, scope)
        assert before_current.outcome == "unknown"
        for name in order:
            request = old_request if name == "old" else new_request
            assert not (await c.temporal.attach_reviewed(request)).replayed
        assert await fingerprint(c, old) == before
        assert (await c.temporal.attach_reviewed(old_request)).replayed
        current = await resolve(c, scope)
        assert current.values == ["Router B"]
        past = await resolve(c, scope, as_of=datetime(2025, 6, 1, tzinfo=UTC))
        assert past.outcome == "resolved" and past.values == ["Router X"]
        projections.append(
            (
                current.outcome,
                current.values,
                current.reasons,
                past.outcome,
                past.values,
                past.reasons,
            )
        )
        assert (await resolve(c, scope, as_of=datetime(2026, 1, 1, tzinfo=UTC))).values == [
            "Router B"
        ]
        changed = old_request.model_copy(deep=True)
        changed.evidence.temporal.value = "False value"
        with pytest.raises(ConflictError):
            await c.temporal.attach_reviewed(changed)
        assert await fingerprint(c, old) == before
    assert projections[0] == projections[1]


async def test_attachment_rejects_unknown_dates_and_unreviewed_supersession(integration_container):
    c = integration_container
    old = await ingest(c, "Router X was used.")
    new = await ingest(c, "Router B is used.", supersedes_id=str(old))
    old_hash = (await fingerprint(c, old))[0]["content_hash"]
    request = attachment(
        old,
        old_hash,
        "Router X was used.",
        "superseded",
        "negative",
        key="negative:old",
        value="Router X",
        mode="historical",
        transition_assertion_id=new,
        reason="historical_transition",
    )
    with pytest.raises(InvalidRequestError, match="bounded"):
        await c.temporal.attach_reviewed(request)
    request.evidence.valid_from = datetime(2025, 1, 1, tzinfo=UTC)
    request.evidence.valid_to = datetime(2026, 1, 1, tzinfo=UTC)
    request.evidence.temporal.legacy_supersession_reason = "factual_correction"
    with pytest.raises(InvalidRequestError, match="reviewed historical transition"):
        await c.temporal.attach_reviewed(request)
    request.evidence.temporal.legacy_supersession_reason = "unknown"
    with pytest.raises(InvalidRequestError, match="reviewed historical transition"):
        await c.temporal.attach_reviewed(request)
    request.evidence.temporal.legacy_supersession_reason = "historical_transition"
    request.evidence.temporal.transition_assertion_id = uuid4()
    with pytest.raises(ConflictError, match="explicitly supersede"):
        await c.temporal.attach_reviewed(request)
    batch = await c.ingestion.begin("test-user", str(uuid4()), 1, 1)
    accepted, rejected, _ = await c.ingestion.append(
        "test-user", batch.id, 1, [request.evidence.model_dump(mode="json")]
    )
    assert accepted == 1 and not rejected
    assert (await c.ingestion.commit("test-user", batch.id)).counts.rejected == 1
    request.evidence.temporal.transition_assertion_id = new
    results = await asyncio.gather(*(c.temporal.attach_reviewed(request) for _ in range(6)))
    assert sum(not result.replayed for result in results) == 1
    assert (await resolve(c, "negative", as_of=datetime(2025, 6, 1, tzinfo=UTC))).values == [
        "Router X"
    ]


async def test_attachment_mcp_http_admin_contract(
    integration_container, raw_token, writer_token, reader_token, serve, mcp_connect
):
    c = integration_container
    target = await ingest(c, "Router Z is installed.")
    target_hash = (await fingerprint(c, target))[0]["content_hash"]
    request = attachment(
        target,
        target_hash,
        "Router Z is installed.",
        "current",
        "contract",
        key="contract:source",
        value="Router Z",
        mode="snapshot",
    ).model_dump(mode="json")
    base = await serve(create_app(c))
    async with mcp_connect(base, writer_token) as writer:
        error, message = await writer.call(
            "attach_reviewed_temporal_evidence", {"request": request}
        )
        assert error and "forbidden" in message
    async with mcp_connect(base, raw_token) as admin:
        error, attached = await admin.call(
            "attach_reviewed_temporal_evidence", {"request": request}
        )
        assert not error and attached["replayed"] is False
    async with httpx2.AsyncClient(
        base_url=base, headers={"Authorization": f"Bearer {raw_token}"}
    ) as admin_http:
        response = await admin_http.post("/api/v1/state/evidence/attach", json=request)
        assert response.status_code == 200
        assert response.json() == {**attached, "replayed": True}
    async with httpx2.AsyncClient(
        base_url=base, headers={"Authorization": f"Bearer {reader_token}"}
    ) as reader_http:
        assert (
            await reader_http.post("/api/v1/state/evidence/attach", json=request)
        ).status_code == 403
    assert (await resolve(c, "contract")).values == ["Router Z"]


async def test_synthetic_twenty_assertion_pilot(integration_container):
    c = integration_container
    # Twenty legacy rows are seeded; only six receive independently reviewed evidence.
    old_text = "Pilot Router X was installed."
    new_text = "Pilot Router B is installed."
    old = await ingest(c, old_text)
    new = await ingest(c, new_text, supersedes_id=str(old))
    unknown = await ingest(c, "Pilot Router U transition date is unknown.")
    conflict_a = await ingest(c, "Pilot Router C1 is installed.")
    conflict_b = await ingest(c, "Pilot Router C2 is installed.")
    incorrect = await ingest(c, "Pilot fact was incorrect.", kind="user_fact")
    corrected = await ingest(
        c, "Pilot corrected fact is accurate.", kind="user_fact", supersedes_id=str(incorrect)
    )
    proposals = [await ingest(c, f"Pilot proposed router {index}.") for index in range(2)]
    fillers = [
        await ingest(
            c,
            f"Pilot independent item {index}.",
            kind="preference" if index < 5 else "plan" if index < 9 else "user_fact",
        )
        for index in range(10)
    ]
    ids = [old, new, unknown, conflict_a, conflict_b, incorrect, corrected, *proposals, *fillers]
    assert len(ids) == 19
    last = await ingest(c, "Pilot final independent preference.", kind="preference")
    ids.append(last)
    assert len(set(ids)) == 20
    # Duplicate text exercises legacy confirmation before the preservation snapshot.
    assert await ingest(c, "Pilot independent item 0.", kind="preference") == fillers[0]
    before = {assertion_id: await fingerprint(c, assertion_id) for assertion_id in ids}
    for scope in ("pilot", "pilot-unknown", "pilot-conflict", "pilot-proposal"):
        assert (await resolve(c, scope)).outcome == "unknown"

    def reviewed(assertion_id, content, status, scope, key, value, mode, **kwargs):
        return attachment(
            assertion_id,
            before[assertion_id][0]["content_hash"],
            content,
            status,
            scope,
            key=key,
            value=value,
            mode=mode,
            **kwargs,
        )

    requests = [
        reviewed(
            old,
            old_text,
            "superseded",
            "pilot",
            "pilot:old",
            "Router X",
            "historical",
            valid_from="2025-01-01T00:00:00Z",
            valid_to="2026-01-01T00:00:00Z",
            transition_assertion_id=new,
            reason="historical_transition",
        ),
        reviewed(
            new,
            new_text,
            "current",
            "pilot",
            "pilot:new",
            "Router B",
            "change",
            valid_from="2026-01-01T00:00:00Z",
        ),
        reviewed(
            conflict_a,
            "Pilot Router C1 is installed.",
            "current",
            "pilot-conflict",
            "pilot:c1",
            "Router C1",
            "snapshot",
        ),
        reviewed(
            conflict_b,
            "Pilot Router C2 is installed.",
            "current",
            "pilot-conflict",
            "pilot:c2",
            "Router C2",
            "snapshot",
        ),
        reviewed(
            proposals[0],
            "Pilot proposed router 0.",
            "current",
            "pilot-proposal",
            "pilot:proposal",
            "Router P",
            "proposal",
        ),
        reviewed(
            proposals[1],
            "Pilot proposed router 1.",
            "current",
            "pilot-proposal",
            "pilot:hypothesis",
            "Router H",
            "hypothesis",
        ),
    ]
    for request in requests:
        await c.temporal.attach_reviewed(request)
    assert {assertion_id: await fingerprint(c, assertion_id) for assertion_id in ids} == before
    assert (await resolve(c, "pilot")).values == ["Router B"]
    historical = await resolve(c, "pilot", as_of=datetime(2025, 6, 1, tzinfo=UTC))
    assert historical.outcome == "resolved" and historical.values == ["Router X"]
    assert (await resolve(c, "pilot-unknown")).outcome == "unknown"
    assert (await resolve(c, "pilot-conflict")).outcome == "conflict"
    assert (await resolve(c, "pilot-proposal")).outcome == "unknown"
    assert (await resolve(c, "pilot", as_of=datetime(2024, 1, 1, tzinfo=UTC))).outcome == "unknown"


async def stage_evidence(container, payload):
    batch = await container.ingestion.begin("test-user", str(uuid4()), 1, 1)
    accepted, rejected, _ = await container.ingestion.append("test-user", batch.id, 1, [payload])
    assert accepted == 1 and not rejected
    return batch.id


async def race_flush_and_attachment(container, request, *, changed_payload):
    payload = request.evidence.model_dump(mode="json")
    if changed_payload:
        payload["confidence"] = 0.5
    batch_id = await stage_evidence(container, payload)
    started = [asyncio.Event(), asyncio.Event()]

    async def attach():
        started[0].set()
        return await container.temporal.attach_reviewed(request)

    async def flush():
        started[1].set()
        return await container.ingestion.commit("test-user", batch_id)

    async with container.database.sessions.begin() as blocker:
        await maintenance_lock(blocker, f"temporal:{request.evidence.temporal.evidence_key}")
        tasks = [asyncio.create_task(attach()), asyncio.create_task(flush())]
        await asyncio.gather(*(event.wait() for event in started))
        # Both operations have started while the shared key remains locked.
        await asyncio.sleep(0)
    results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)
    async with container.database.sessions() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(TemporalEvidenceRow)
            .where(TemporalEvidenceRow.evidence_key == request.evidence.temporal.evidence_key)
        )
    assert count == 1
    return results


async def test_flush_and_admin_attachment_share_key_lock(integration_container):
    c = integration_container
    for changed_payload in (False, True):
        label = str(changed_payload).lower()
        target = await ingest(c, f"Race target {changed_payload}.")
        target_hash = (await fingerprint(c, target))[0]["content_hash"]
        request = attachment(
            target,
            target_hash,
            f"Race target {changed_payload}.",
            "current",
            f"race-{label}",
            key=f"race:{label}",
            value="Router X",
            mode="snapshot",
        )
        attached, flushed = await race_flush_and_attachment(
            c, request, changed_payload=changed_payload
        )
        if changed_payload:
            assert isinstance(attached, ConflictError) or flushed.counts.rejected == 1
            assert not isinstance(flushed, Exception)
        else:
            assert not isinstance(attached, Exception)
            assert not isinstance(flushed, Exception)
            assert attached.replayed != (flushed.items[0].evidence_outcome == "replayed")


async def test_attachment_races_metadata_update_and_replays_after_change(integration_container):
    c = integration_container
    target = await ingest(c, "Metadata race target.")
    before = await fingerprint(c, target)
    request = attachment(
        target,
        before[0]["content_hash"],
        "Metadata race target.",
        "current",
        "metadata-race",
        key="metadata-race:key",
        value="Router X",
        mode="snapshot",
    )
    started = asyncio.Event()

    async def attach():
        started.set()
        return await c.temporal.attach_reviewed(request)

    async with c.database.sessions.begin() as blocker:
        await maintenance_lock(blocker, "temporal:metadata-race:key")
        task = asyncio.create_task(attach())
        await started.wait()
        await c.maintenance.update(
            "test-user",
            MetadataUpdate(
                assertion_id=target,
                expected_revision=before[0]["revision"],
                idempotency_key="metadata-race-update",
                changes={"status": "uncertain"},
            ),
        )
    with pytest.raises(ConflictError, match="does not match target"):
        await asyncio.wait_for(task, 10)

    replay_target = await ingest(c, "Replay target.")
    replay_before = await fingerprint(c, replay_target)
    replay_request = attachment(
        replay_target,
        replay_before[0]["content_hash"],
        "Replay target.",
        "current",
        "metadata-replay",
        key="metadata-replay:key",
        value="Router Y",
        mode="snapshot",
    )
    assert not (await c.temporal.attach_reviewed(replay_request)).replayed
    await c.maintenance.update(
        "test-user",
        MetadataUpdate(
            assertion_id=replay_target,
            expected_revision=replay_before[0]["revision"],
            idempotency_key="metadata-replay-update",
            changes={"status": "uncertain"},
        ),
    )
    changed = await fingerprint(c, replay_target)
    assert (await c.temporal.attach_reviewed(replay_request)).replayed
    assert await fingerprint(c, replay_target) == changed


async def test_attachment_races_supersession_and_locks_both_rows(integration_container):
    c = integration_container
    target = await ingest(c, "Supersession race target.")
    target_hash = (await fingerprint(c, target))[0]["content_hash"]
    current_request = attachment(
        target,
        target_hash,
        "Supersession race target.",
        "current",
        "supersession-race",
        key="supersession-race:key",
        value="Router X",
        mode="snapshot",
    )
    started = asyncio.Event()

    async def attach():
        started.set()
        return await c.temporal.attach_reviewed(current_request)

    async with c.database.sessions.begin() as blocker:
        await maintenance_lock(blocker, "temporal:supersession-race:key")
        task = asyncio.create_task(attach())
        await started.wait()
        successor = await ingest(c, "Supersession race successor.", supersedes_id=str(target))
    with pytest.raises(ConflictError, match="does not match target"):
        await asyncio.wait_for(task, 10)

    historical = attachment(
        target,
        target_hash,
        "Supersession race target.",
        "superseded",
        "supersession-race",
        key="supersession-race:historical",
        value="Router X",
        mode="historical",
        valid_from="2025-01-01T00:00:00Z",
        valid_to="2026-01-01T00:00:00Z",
        transition_assertion_id=successor,
        reason="historical_transition",
    )
    for locked_id in (target, successor):
        async with c.database.sessions.begin() as blocker:
            await blocker.execute(
                select(AssertionRow.id).where(AssertionRow.id == locked_id).with_for_update()
            )
            with pytest.raises(ConflictError, match="being modified"):
                await c.temporal.attach_reviewed(historical)
    assert not (await c.temporal.attach_reviewed(historical)).replayed


async def test_new_attachment_validates_metadata_refs_but_replay_uses_stored_payload(
    integration_container,
):
    c = integration_container
    target = await ingest(c, "Metadata reference target.")
    before = await fingerprint(c, target)
    request = attachment(
        target,
        before[0]["content_hash"],
        "Metadata reference target.",
        "current",
        "metadata-refs",
        key="metadata-refs:key",
        value="Router X",
        mode="snapshot",
    )
    invalid_context = request.model_dump(mode="json")
    invalid_context["evidence"]["context_id"] = str(uuid4())
    with pytest.raises(InvalidRequestError, match="context_id was not found"):
        await c.temporal.attach_reviewed(ReviewedTemporalAttachment.model_validate(invalid_context))

    invalid_artifact = request.model_dump(mode="json")
    invalid_artifact["evidence"]["provenance"] = [{"kind": "artifact", "locator": str(uuid4())}]
    with pytest.raises(InvalidRequestError, match="artifact provenance"):
        await c.temporal.attach_reviewed(
            ReviewedTemporalAttachment.model_validate(invalid_artifact)
        )
    async with c.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(TemporalEvidenceRow)) == 0
    assert await fingerprint(c, target) == before

    context_id = uuid4()
    await c.maintenance.context(ContextInput(id=context_id, name="Synthetic attachment context"))
    valid = request.model_dump(mode="json")
    valid["evidence"]["context_id"] = str(context_id)
    accepted = ReviewedTemporalAttachment.model_validate(valid)
    assert not (await c.temporal.attach_reviewed(accepted)).replayed
    async with c.database.sessions.begin() as session:
        await session.execute(
            text("DELETE FROM knowledge_contexts WHERE id=:id"), {"id": context_id}
        )
    assert (await c.temporal.attach_reviewed(accepted)).replayed
    assert await fingerprint(c, target) == before
