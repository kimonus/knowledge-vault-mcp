"""Source-truth regressions; these checks do not substitute for semantic review."""

import json
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256

import pytest

from .client_packets import build_client_packets
from .corpus import cases
from .evaluation import truth_card_digest, validate_truth_audits
from .judgments import score_judgments


@pytest.mark.parametrize("case", cases(), ids=lambda case: case.id)
def test_every_gold_observation_instant_is_explicit_in_source(case):
    for claim in case.claims:
        if observed := claim.payload.get("observed_at"):
            assert observed in case.source
            assert datetime.fromisoformat(observed).utcoffset().total_seconds() == 0


def test_multilingual_sources_explicitly_attribute_preferences():
    selected = [case for case in cases() if case.category == "multilingual"]
    assert len(selected) == 6
    for case in selected:
        assert "deklaruje preferencję" in case.source or "выражает предпочтение" in case.source
        assert case.claims[0].payload["kind"] == "preference"


def test_fixture_identity_is_available_without_revealing_answers():
    for split in ("dev", "held_out"):
        manifest = build_client_packets(cases(), split=split)
        for case, packet in zip(
            (case for case in cases() if case.split == split), manifest["packets"], strict=True
        ):
            if case.state:
                for claim in case.claims:
                    assert (
                        claim.payload["temporal"]["identity"] == packet["state_request"]["identity"]
                    )
            assert "claims" not in packet and "outcome" not in packet and "values" not in packet


def test_audit_cannot_reuse_old_source_truth_binding():
    from dataclasses import replace

    from .test_evaluation_modes import CASE, audit

    original = CASE
    changed = replace(original, source=original.source + " Clarified source.")
    assert truth_card_digest(original) != truth_card_digest(changed)
    with pytest.raises(ValueError, match="source digest"):
        validate_truth_audits((changed,), [audit()], "solo_operator")


def test_source_remediation_preserves_fixture_identifiers_and_gold_contract():
    # Freeze the revised contract, including attributed conflict reports.
    first = cases()
    contract = [
        {key: value for key, value in asdict(case).items() if key != "source"} for case in first
    ]
    assert (
        sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
        == "756abed5272cb10198acf7d387878fa98229693945eda9a2ccdf0d69c9bcce67"
    )
    assert len({case.id for case in first}) == 63
    keys = [claim.key for case in first for claim in case.claims]
    assert len(keys) == len(set(keys))
    for case in first:
        if case.id.startswith("conflict-"):
            assert case.state.outcome == "conflict"
            assert all(claim.payload["evidence_state"] == "reported" for claim in case.claims)
            assert "Neither report" in case.source


def test_historical_corpus_digest_cannot_certify_corrected_experiment():
    manifest = build_client_packets(cases(), split="dev", repetitions=3)
    manifest["corpus_sha256"] = "1afc3456c9f47938e45b33c1d5497c5861d961c219dd91a5a78081bb5d8a279a"
    with pytest.raises(ValueError, match="packet manifest differs"):
        score_judgments(cases(), [], packet_manifest=manifest, evaluation_mode="solo_operator")


def test_pre_attribution_manifest_is_stale_even_with_unchanged_packet_text():
    manifest = build_client_packets(cases(), split="dev", repetitions=3)
    manifest["corpus_sha256"] = "2764de846bffe48b769e0f4b1d92386018936ca7f3941d81898229a084387128"
    with pytest.raises(ValueError, match="packet manifest differs"):
        score_judgments(cases(), [], packet_manifest=manifest, evaluation_mode="solo_operator")


@pytest.mark.parametrize("case", [case for case in cases() if case.id.startswith("conflict-")])
def test_conflicting_locations_remain_attributed_competing_reports(case):
    assert case.state.outcome == "conflict"
    assert case.state.values == ("room-a", "room-b")
    assert [claim.key for claim in case.claims] == [case.id + "-a", case.id + "-b"]
    for claim, prefix in zip(case.claims, ("One", "Another"), strict=True):
        assert claim.payload["content"].startswith(prefix + " unverified note reports that ")
        assert claim.payload["temporal"]["mode"] == "snapshot"
        assert claim.payload["evidence_state"] == "reported"
        assert claim.payload["observed_at"] == "2026-01-01T00:00:00Z"


def test_categorical_conflict_approval_is_invalid_after_attribution_repair():
    from copy import deepcopy
    from dataclasses import replace

    from .test_evaluation_modes import audit

    corrected = next(case for case in cases() if case.id == "conflict-3")
    old_claims = deepcopy(corrected.claims)
    for claim, value in zip(old_claims, ("room-a", "room-b"), strict=True):
        claim.payload["content"] = f"The thermostat is installed in {value}."
    old = replace(corrected, claims=old_claims)
    stale = audit()
    stale.update(
        case_id=corrected.id,
        source_sha256=sha256(old.source.encode()).hexdigest(),
        truth_card_sha256=truth_card_digest(old),
    )
    stale["provenance"]["evidence_sha256"] = stale["source_sha256"]
    assert corrected.source == old.source
    with pytest.raises(ValueError, match="card digest"):
        validate_truth_audits((corrected,), [stale], "solo_operator")


@pytest.mark.parametrize("field", ["source_sha256", "truth_card_sha256"])
def test_source_or_gold_hash_cannot_masquerade_as_completed_review(field):
    from .test_evaluation_modes import CASE, audit

    receipt = audit()
    receipt["artifact_sha256"] = receipt[field]
    with pytest.raises(ValueError, match="completed review"):
        validate_truth_audits((CASE,), [receipt], "solo_operator")
