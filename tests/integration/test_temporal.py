import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from knowledge_vault.domain.models import AssertionInput
from knowledge_vault.domain.temporal import ResolutionRequest
from knowledge_vault.persistence.tables import AssertionRow, TemporalEvidenceRow
from knowledge_vault.services.errors import ConflictError, InvalidRequestError, NotFoundError

pytestmark = pytest.mark.integration


def identity(scope="home"):
    return {
        "subject": "router",
        "property": "wireguard.endpoint",
        "scope": scope,
        "state_type": "configuration",
    }


def claim(key, value, observed=None, mode="snapshot", scope="home", **kw):
    return {
        "content": f"The WireGuard endpoint is {value}.",
        "kind": "configuration",
        "origin": "user",
        "evidence_state": "reported",
        "observed_at": observed,
        "temporal": {
            "identity": identity(scope),
            "evidence_key": key,
            "value": value,
            "mode": mode,
            **kw.pop("temporal", {}),
        },
        **kw,
    }


async def stage(c, claims, key=None):
    batch = await c.ingestion.begin("test-user", key or str(uuid4()), 1, len(claims))
    accepted, rejected, _ = await c.ingestion.append("test-user", batch.id, 1, claims)
    assert accepted == len(claims) and not rejected
    return batch.id


async def ingest(c, claims, key=None):
    batch = await stage(c, claims, key)
    return await c.ingestion.commit("test-user", batch)


async def resolve(c, scope="home", **kw):
    return await c.temporal.resolve(ResolutionRequest(identity=identity(scope), **kw))


async def test_out_of_order_reverse_equivalence_historical_and_rebuild(integration_container):
    c = integration_container
    logical = []
    for scope, order in [("forward", [1, 31]), ("reverse", [31, 1])]:
        for day in order:
            value = "router-x" if day == 1 else "mikrotik"
            await ingest(
                c,
                [
                    claim(
                        f"{scope}/{day}",
                        value,
                        f"2026-01-{day:02}T00:00:00Z",
                        "snapshot" if day == 1 else "change",
                        scope,
                    )
                ],
            )
        result = await resolve(c, scope)
        assert result.outcome == "resolved" and result.values == ["mikrotik"]
        assert len(result.history) == 2
        logical.append(
            [
                (e.assertion.temporal.value, e.disposition, e.ordering_at)
                for e in sorted(result.history, key=lambda e: e.ordering_at)
            ]
        )
        past = await resolve(c, scope, as_of=datetime(2026, 1, 1, tzinfo=UTC))
        assert past.outcome == "resolved" and past.values == ["router-x"]
    assert logical[0] == logical[1]
    # Rebuilding is simply another pure projection from the same durable evidence.
    assert (await resolve(c, "forward")).model_dump() == (await resolve(c, "forward")).model_dump()


async def test_backdate_proposal_recollection_and_unknown_dates(integration_container):
    c = integration_container
    await ingest(
        c,
        [
            claim(
                "effective",
                "mikrotik",
                "2026-01-31T00:00:00Z",
                "change",
                valid_from="2025-12-15T00:00:00Z",
            ),
            claim(
                "proposal", "router-p", "2026-02-01T00:00:00Z", "proposal", kind="considered_option"
            ),
            claim(
                "past",
                "router-x",
                "2026-02-15T00:00:00Z",
                "historical",
                valid_from="2025-01-01T00:00:00Z",
                valid_to="2025-12-15T00:00:00Z",
            ),
        ],
    )
    assert (await resolve(c)).values == ["mikrotik"]
    assert (await resolve(c, as_of=datetime(2026, 1, 1, tzinfo=UTC))).values == ["mikrotik"]
    assert (await resolve(c, as_of=datetime(2025, 2, 1, tzinfo=UTC))).values == ["router-x"]
    for key, value in [("missing-a", "a"), ("missing-b", "b")]:
        await ingest(c, [claim(key, value, scope="missing")])
    missing = await resolve(c, "missing")
    assert missing.outcome == "ambiguous" and not missing.supporting_keys


async def test_correction_before_target_reimport_and_known_at(integration_container):
    c = integration_container
    original = claim("original", "router-x", "2026-01-01T00:00:00Z")
    corrected = claim(
        "corrected",
        "mikrotik",
        "2026-02-01T00:00:00Z",
        "correction",
        temporal={"corrects_key": "original"},
    )
    await ingest(c, [corrected])
    assert (await resolve(c)).outcome == "ambiguous"
    await ingest(c, [original])
    result = await resolve(c)
    assert result.values == ["mikrotik"] and result.outcome == "resolved"
    by_key = {e.evidence_key: e for e in result.history}
    assert by_key["corrected"].ordering_at == by_key["original"].ordering_at
    again = await ingest(c, [original, corrected])
    assert [i.evidence_outcome for i in again.items] == ["replayed", "replayed"]
    assert (await resolve(c)).evidence_total == 2
    assert (await resolve(c, known_at=datetime(2020, 1, 1, tzinfo=UTC))).outcome == "unknown"
    # A conflicting payload cannot reuse an immutable key; other items still commit.
    bad = await ingest(c, [{**original, "confidence": 0.5}, claim("good", "good", scope="other")])
    assert bad.counts.rejected == 1 and len(bad.items) == 1


async def test_concurrent_imports_and_confirmations(integration_container):
    c = integration_container
    claims = [
        claim("old", "router-x", "2026-01-01T00:00:00Z"),
        claim("new", "mikrotik", "2026-01-31T00:00:00Z", "change"),
    ]
    batches = await asyncio.gather(*(stage(c, [value]) for value in claims * 2))
    results = await asyncio.gather(*(c.ingestion.commit("test-user", batch) for batch in batches))
    assert sum(i.evidence_outcome == "inserted" for r in results for i in r.items) == 2
    replay = await c.ingestion.commit("test-user", batches[0])
    assert replay == results[0]
    assert (await resolve(c)).values == ["mikrotik"]
    # Same value from a new source preserves independent provenance, no new legacy assertion.
    await ingest(c, [claim("confirmation", "mikrotik", "2026-02-15T00:00:00Z")])
    result = await resolve(c)
    assert result.evidence_total == 3 and result.values == ["mikrotik"]
    async with c.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(AssertionRow)) == 2
        assert await session.scalar(select(func.count()).select_from(TemporalEvidenceRow)) == 3


async def test_scope_conflicts_legacy_diagnostics_and_get(integration_container):
    c = integration_container
    legacy = await ingest(
        c, [{"content": "A legacy assertion.", "kind": "user_fact", "origin": "user"}]
    )
    assert (await c.search.get(legacy.assertion_ids[0])).observed_at is None
    await ingest(
        c,
        [
            claim("homelab", "x", "2026-01-01T00:00:00Z"),
            claim("production", "y", "2026-01-01T00:00:00Z", scope="prod"),
        ],
    )
    assert (await resolve(c)).values == ["x"] and (await resolve(c, "prod")).values == ["y"]
    await ingest(c, [claim("contradiction", "z", "2026-01-01T00:00:00Z")])
    result = await resolve(c)
    assert result.outcome == "conflict" and result.values == ["x", "z"]
    assert len(result.competing_keys) == 2
    diag = await c.temporal.diagnostics()
    assert (diag.fully_classified, diag.unresolved, diag.evidence_total) == (3, 1, 3)
    view = await c.search.get(result.history[0].assertion_id)
    assert view.temporal_identities[0].scope == "home"


async def test_rollback_immutability_and_hard_delete(integration_container):
    c = integration_container
    batch = await stage(c, [claim("first", "x"), claim("broken", "y", artifact_ids=[str(uuid4())])])
    with pytest.raises(NotFoundError):
        await c.ingestion.commit("test-user", batch)
    assert (await resolve(c)).evidence_total == 0
    result = await ingest(c, [claim("first", "x")])
    with pytest.raises(IntegrityError):
        async with c.database.sessions.begin() as session:
            await session.execute(text("UPDATE temporal_evidence SET payload_hash=payload_hash"))
    preview = await c.administration.preview_forgetting("test-user", result.assertion_ids)
    await c.administration.confirm_forgetting("test-user", preview["confirmation_token"])
    assert (await resolve(c)).evidence_total == 0


async def test_deleted_correction_never_revives_invalidated_claim(integration_container):
    c = integration_container
    old = await ingest(c, [claim("old", "x", "2026-01-01T00:00:00Z")])
    corrected = await ingest(
        c,
        [
            claim(
                "fixed", "y", "2026-02-01T00:00:00Z", "correction", temporal={"corrects_key": "old"}
            )
        ],
    )
    assert (await resolve(c)).values == ["y"]
    preview = await c.administration.preview_forgetting("test-user", corrected.assertion_ids)
    assert preview["temporal_evidence_count"] == preview["temporal_retirement_count"] == 1
    await c.administration.confirm_forgetting("test-user", preview["confirmation_token"])
    result = await resolve(c)
    assert result.outcome == "unknown" and not result.supporting_keys
    assert result.history[0].disposition == "corrected"
    # A legacy correction cannot update only the text-level status of structured knowledge.
    with pytest.raises(ConflictError, match="corrects_key"):
        await ingest(
            c,
            [
                {
                    "content": "A legacy correction.",
                    "kind": "configuration",
                    "origin": "user",
                    "supersedes_id": str(old.assertion_ids[0]),
                }
            ],
        )


async def test_deleted_target_leaves_correction_unresolved(integration_container):
    c = integration_container
    old = await ingest(c, [claim("old", "x", "2026-01-01T00:00:00Z")])
    await ingest(
        c,
        [
            claim(
                "fixed", "y", "2026-02-01T00:00:00Z", "correction", temporal={"corrects_key": "old"}
            )
        ],
    )
    preview = await c.administration.preview_forgetting("test-user", old.assertion_ids)
    await c.administration.confirm_forgetting("test-user", preview["confirmation_token"])
    result = await resolve(c)
    assert result.outcome == "ambiguous" and "missing_correction_target" in result.reasons


async def test_reviewed_legacy_enrichment_keeps_retired_history(integration_container):
    c = integration_container
    original = {
        "content": "The WireGuard endpoint is x.",
        "kind": "configuration",
        "origin": "user",
    }
    old = await ingest(c, [original])
    await ingest(
        c,
        [
            {
                "content": "A legacy corrected claim.",
                "kind": "configuration",
                "origin": "user",
                "supersedes_id": str(old.assertion_ids[0]),
            }
        ],
    )
    with pytest.raises(ConflictError, match="review"):
        await ingest(c, [claim("review-required", "x", "2026-01-01T00:00:00Z")])
    enriched = await ingest(
        c,
        [
            claim("reviewed-retired", "x", "2026-01-01T00:00:00Z", status="superseded"),
            claim("new-state", "y", "2026-02-01T00:00:00Z"),
        ],
    )
    assert enriched.assertion_ids[0] == old.assertion_ids[0]
    result = await resolve(c)
    assert result.outcome == "resolved" and result.values == ["y"]
    assert (
        next(e for e in result.history if e.evidence_key == "reviewed-retired").disposition
        == "corrected"
    )


@pytest.mark.parametrize("temporal", [False, True])
async def test_correction_retry_and_concurrent_idempotency(integration_container, temporal):
    c = integration_container
    if temporal:
        await ingest(c, [claim("original", "x", "2026-01-01T00:00:00Z")])
        raw = claim(
            "fixed",
            "y",
            "2026-02-01T00:00:00Z",
            "correction",
            temporal={"corrects_key": "original"},
        )
    else:
        old = await ingest(
            c, [{"content": "Original legacy text.", "kind": "configuration", "origin": "user"}]
        )
        raw = {
            "content": "Correct legacy text.",
            "kind": "configuration",
            "origin": "user",
            "supersedes_id": str(old.assertion_ids[0]),
        }
    request = AssertionInput.model_validate(raw)
    results = await asyncio.gather(
        *(c.administration.correct("test-user", "same-correction", request) for _ in range(6))
    )
    assert all(result == results[0] for result in results)
    assert await c.administration.correct("test-user", "same-correction", request) == results[0]
    with pytest.raises(ConflictError):
        await c.administration.correct(
            "test-user", "same-correction", request.model_copy(update={"confidence": 0.5})
        )


async def test_resolution_bound_fails_closed_after_complete_ingestion(integration_container):
    c = integration_container
    for start in range(0, 1000, 100):
        await ingest(
            c,
            [
                claim(f"source:{i:04}", "x", "2026-01-01T00:00:00Z")
                for i in range(start, start + 100)
            ],
        )
    page = await resolve(c, history_limit=1)
    assert page.outcome == "resolved" and page.evidence_total == 1000 and len(page.history) == 1
    await ingest(c, [claim("source:1000", "x", "2026-01-01T00:00:00Z")])
    with pytest.raises(InvalidRequestError, match="no partial winner"):
        await resolve(c)
