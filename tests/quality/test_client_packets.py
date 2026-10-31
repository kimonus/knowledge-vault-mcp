"""Blinded client packets disclose tasks but no machine-readable gold labels."""

import pytest

from .client_packets import build_client_packets
from .corpus import cases


def test_packets_are_bounded_blinded_and_repeatable():
    first = build_client_packets(cases(), split="dev", repetitions=2)
    second = build_client_packets(cases(), split="dev", repetitions=2)
    assert first == second
    assert first["case_count"] == 41
    assert first["run_count"] == 82
    assert len(first["corpus_sha256"]) == 64
    assert {packet["run_id"] for packet in first["packets"]} == {"r01", "r02"}
    assert all(
        set(packet) == {"case_id", "run_id", "source", "question", "state_request"}
        for packet in first["packets"]
    )
    assert all(packet["source"] for packet in first["packets"])
    assert not any("expected" in str(packet) for packet in first["packets"])


def test_held_out_and_invalid_packet_requests():
    held_out = build_client_packets(cases(), split="held_out")
    assert held_out["case_count"] == 22
    assert held_out["run_count"] == 22
    with pytest.raises(ValueError, match="repetitions"):
        build_client_packets(cases(), repetitions=11)
    with pytest.raises(ValueError, match="split"):
        build_client_packets(cases(), split="unknown")
