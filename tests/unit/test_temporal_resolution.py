from datetime import UTC, datetime
from itertools import permutations
from uuid import NAMESPACE_URL, uuid5

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from knowledge_vault.domain.models import AssertionInput
from knowledge_vault.domain.temporal import ResolutionRequest, StateIdentity
from knowledge_vault.services.errors import InvalidRequestError
from knowledge_vault.services.temporal import Evidence, resolve_evidence, window

IDENTITY = {
    "subject": "homelab.router",
    "property": "wireguard.endpoint",
    "scope": "home",
    "state_type": "configuration",
}
NOW = datetime(2026, 3, 20, tzinfo=UTC)


def item(key="a", value="router-x", observed="2026-01-01T00:00:00Z", mode="snapshot", **kw):
    temporal = {
        "identity": IDENTITY,
        "evidence_key": key,
        "value": value,
        "mode": mode,
        **kw.pop("temporal", {}),
    }
    return AssertionInput.model_validate(
        {
            "content": f"WireGuard endpoint is {value}.",
            "kind": "configuration",
            "origin": "user",
            "evidence_state": "reported",
            "observed_at": observed,
            "temporal": temporal,
            **kw,
        }
    )


def evidence(claim):
    return Evidence(claim.temporal.evidence_key, uuid5(NAMESPACE_URL, claim.content), NOW, claim)


def resolve(claims, **kw):
    return resolve_evidence(
        [evidence(c) for c in claims],
        ResolutionRequest(identity=StateIdentity(**IDENTITY), **kw),
        now=NOW,
    )


def logical(result):
    return result.model_dump(exclude={"history", "known_at"}), [
        (e.evidence_key, e.assertion.temporal.value, e.disposition, e.ordering_at, e.ordering_basis)
        for e in result.history
    ]


def test_all_permutations_and_history():
    claims = [
        item(),
        item("b", "mikrotik", "2026-01-31T00:00:00Z", "change"),
        item("c", "future-router", "2026-02-15T00:00:00Z", "proposal"),
        item("d", "ancient-router", "2026-03-01T00:00:00Z", "historical"),
    ]
    reference = logical(resolve(claims))
    for order in permutations(claims):
        result = resolve(order)
        assert logical(result) == reference
        assert result.values == ["mikrotik"] and result.outcome == "resolved"
    old = resolve(claims, as_of=datetime(2026, 1, 1, tzinfo=UTC))
    assert old.outcome == "resolved" and old.values == ["router-x"]
    # Observation timestamps never fabricate applicability between observed points.
    assert resolve(claims, as_of=datetime(2026, 1, 15, tzinfo=UTC)).outcome == "unknown"


def test_effective_dates_and_provenance_are_distinct():
    claim = item(
        "b",
        "mikrotik",
        "2026-01-31T00:00:00Z",
        "change",
        valid_from="2025-12-15T00:00:00Z",
        provenance=[
            {
                "kind": "conversation",
                "locator": "source:jan31",
                "conversation_id": "source-31",
                "message_ids": ["message-7"],
                "timestamp_precision": "day",
                "timestamp_basis": "conversation",
            }
        ],
    )
    result = resolve([claim], as_of=datetime(2026, 1, 1, tzinfo=UTC))
    assert result.values == ["mikrotik"] and result.outcome == "resolved"
    assert result.history[0].ordering_at.month == 12
    assert result.history[0].assertion.observed_at.month == 1
    assert result.history[0].assertion.provenance[0].message_ids == ["message-7"]


def test_superseded_historical_requires_reviewed_source_and_transition_reason():
    base = {
        "status": "superseded",
        "evidence_state": "verified",
        "last_verified_at": "2026-03-01T00:00:00Z",
        "sources": [{"url": "https://example.org/router-record"}],
        "valid_from": "2025-01-01T00:00:00Z",
        "valid_to": "2026-01-01T00:00:00Z",
    }
    temporal = {"observation_basis": "source"}
    point = datetime(2025, 6, 1, tzinfo=UTC)
    for reason in ("unknown", "factual_correction"):
        false_history = item(
            mode="historical",
            temporal={**temporal, "legacy_supersession_reason": reason},
            **base,
        )
        assert resolve([false_history], as_of=point).outcome == "unknown"
    unverified = item(
        mode="historical",
        temporal={**temporal, "legacy_supersession_reason": "historical_transition"},
        **{**base, "evidence_state": "reported"},
    )
    assert resolve([unverified], as_of=point).outcome == "unknown"
    reviewed = item(
        mode="historical",
        temporal={**temporal, "legacy_supersession_reason": "historical_transition"},
        **base,
    )
    assert resolve([reviewed], as_of=point).values == ["router-x"]
    assert resolve([reviewed]).outcome == "unknown"


@pytest.mark.parametrize("missing", [None, "2026-01-01T00:00:00Z"])
def test_no_arbitrary_winner(missing):
    claims = [item(observed=missing), item("b", "mikrotik", missing)]
    for order in permutations(claims):
        result = resolve(order)
        assert result.outcome == ("ambiguous" if missing is None else "conflict")
        assert result.supporting_keys == []
        assert result.competing_keys == ["a", "b"]


def test_corrections_retroactively_fix_claim_and_do_not_create_change():
    original = item()
    correction = item(
        "b", "mikrotik", "2026-02-01T00:00:00Z", "correction", temporal={"corrects_key": "a"}
    )
    transition = item("c", "router-c", "2026-01-15T00:00:00Z", "change")
    for order in permutations([original, correction, transition]):
        result = resolve(order)
        assert result.outcome == "resolved" and result.values == ["router-c"]
        assert result.history[0].disposition == "corrected"
        assert result.history[1].ordering_at == original.observed_at
        past = resolve(order, as_of=datetime(2026, 1, 1, tzinfo=UTC))
        assert past.outcome == "resolved" and past.values == ["mikrotik"]


def test_dangling_cyclic_and_unconfirmed_corrections():
    a = item("a", mode="correction", temporal={"corrects_key": "b"})
    b = item("b", "other", mode="correction", temporal={"corrects_key": "a"})
    assert resolve([a]).reasons == ["missing_correction_target", "no_applicable_confirmed_state"]
    assert "cyclic_correction" in resolve([a, b]).reasons
    weak = item(
        "b", "other", mode="correction", evidence_state="inferred", temporal={"corrects_key": "a"}
    )
    result = resolve([item(), weak])
    assert result.outcome == "ambiguous" and "unconfirmed_correction" in result.reasons


def test_repeated_confirmation_does_not_create_transition():
    result = resolve([item(), item("b", observed="2026-02-01T00:00:00Z")])
    assert result.outcome == "resolved" and result.values == ["router-x"]
    assert len(result.history) == 2
    assert resolve([item(observed=None)]).outcome == "ambiguous"
    assert "unknown_chronology" in resolve([item(observed=None)]).reasons


def test_reliability_and_intervals_do_not_silently_overwrite():
    older = item(
        evidence_state="verified",
        last_verified_at="2026-01-01T00:00:00Z",
        provenance=[{"kind": "command", "locator": "observation:router"}],
    )
    newer = item("b", "other", "2026-02-01T00:00:00Z", "change")
    assert resolve([older, newer]).outcome == "conflict"
    bounded = item(valid_from="2025-12-01T00:00:00Z", valid_to="2026-03-01T00:00:00Z")
    assert resolve([bounded, newer], as_of=datetime(2026, 2, 1, tzinfo=UTC)).outcome == "conflict"
    assert resolve([bounded]).outcome == "unknown"  # expired by current query
    assert resolve([item(observed="2027-01-01T00:00:00Z")]).outcome == "unknown"
    assert resolve([item(status="disputed")]).outcome == "ambiguous"


def test_precision_and_unknown_bounds():
    coarse = item(temporal={"observed_precision": "month"})
    same_month = item("b", "other", "2026-01-31T00:00:00Z", "change")
    assert resolve([coarse, same_month]).outcome == "conflict"
    for precision, expected in [
        ("day", (2026, 1, 2)),
        ("month", (2026, 2, 1)),
        ("year", (2027, 1, 1)),
    ]:
        upper = window(datetime(2026, 1, 1, tzinfo=UTC), precision)[1]
        assert (upper.year, upper.month, upper.day) == expected
    assert window(datetime(2026, 12, 1, tzinfo=UTC), "month")[1].year == 2027
    assert window(NOW, "unknown") is None
    partial = item(valid_from="2026-01-01T00:00:00Z", temporal={"valid_from_precision": "month"})
    assert resolve([partial], as_of=datetime(2026, 1, 15, tzinfo=UTC)).outcome == "ambiguous"
    unknown = item(valid_from="2026-01-01T00:00:00Z", temporal={"valid_from_precision": "unknown"})
    assert resolve([unknown]).outcome == "ambiguous"


def test_history_pagination_resolves_entire_property():
    claims = [item(), item("b", "mikrotik", "2026-01-31T00:00:00Z", "change")]
    first = resolve(claims, history_limit=1)
    second = resolve(claims, history_after=first.next_history_after, history_limit=1)
    assert first.values == second.values == ["mikrotik"]
    assert first.next_history_after == "a" and second.next_history_after is None


def test_correction_chains_and_competing_corrections_are_order_independent():
    original = item()
    first = item("b", "y", "2026-02-01T00:00:00Z", "correction", temporal={"corrects_key": "a"})
    second = item("c", "z", "2026-02-02T00:00:00Z", "correction", temporal={"corrects_key": "b"})
    changed = item("d", "w", "2026-01-15T00:00:00Z", "change")
    for order in permutations([original, first, second, changed]):
        assert resolve(order).values == ["w"]
        past = resolve(order, as_of=datetime(2026, 1, 1, tzinfo=UTC))
        assert past.outcome == "resolved" and past.values == ["z"]
    competing = item("c", "z", "2026-03-01T00:00:00Z", "correction", temporal={"corrects_key": "a"})
    assert resolve([original, first, competing]).outcome == "conflict"


def test_historical_recollection_does_not_invent_an_open_interval():
    past = item(mode="historical", valid_from="2026-01-01T00:00:00Z")
    result = resolve([past], as_of=datetime(2026, 2, 1, tzinfo=UTC))
    assert result.outcome == "ambiguous" and "uncertain_validity" in result.reasons


def test_unstructured_evidence_is_a_domain_error():
    bad = Evidence(
        "invalid",
        uuid5(NAMESPACE_URL, "invalid"),
        NOW,
        AssertionInput(content="Legacy text", kind="configuration", origin="user"),
    )
    with pytest.raises(InvalidRequestError, match="structured metadata"):
        resolve_evidence([bad], ResolutionRequest(identity=StateIdentity(**IDENTITY)), now=NOW)


@given(st.permutations([0, 1, 2, 3, 4]))
def test_rebuild_permutation_property(order):
    claims = [item(str(i), str(i), f"2026-01-{i + 1:02}T00:00:00Z", "change") for i in range(5)]
    assert logical(resolve([claims[i] for i in order])) == logical(resolve(claims))


@pytest.mark.parametrize(
    "changes",
    [
        {"supersedes_id": "00000000-0000-0000-0000-000000000001"},
        {"kind": "plan"},
        {"temporal_role": "historical"},
        {"temporal": {"mode": "correction"}},
        {"temporal": {"corrects_key": "a"}},
        {"temporal": {"value": " padded "}},
        {"temporal": {"evidence_key": " a "}},
        {"temporal": {"value": "password=synthetic-long-password"}},
    ],
)
def test_temporal_validation_and_secrets(changes):
    with pytest.raises(ValidationError):
        item(**changes)
