from uuid import UUID

import pytest

from knowledge_vault.services.cursors import Cursor, CursorCodec, InvalidCursorError
from knowledge_vault.services.ranking import reciprocal_rank_fusion


def test_rank_fusion_is_deterministic_and_weighted() -> None:
    results = reciprocal_rank_fusion(["a", "b", "c"], ["b", "d", "a"])
    assert [item.item for item in results] == ["b", "a", "c", "d"]
    assert results[0].score > results[-1].score
    assert results == reciprocal_rank_fusion(["a", "b", "c"], ["b", "d", "a"])


def test_rank_fusion_text_fallback() -> None:
    results = reciprocal_rank_fusion(["a", "b"], [])
    assert [item.item for item in results] == ["a", "b"]


def test_cursor_round_trip_and_tamper_rejection() -> None:
    codec = CursorCodec("secret")
    expected = Cursor("queryhash", 0.123, UUID("00000000-0000-0000-0000-000000000001"))
    value = codec.encode(expected)
    assert codec.decode(value) == expected
    with pytest.raises(InvalidCursorError):
        codec.decode(value[:-2] + "aa")
    with pytest.raises(InvalidCursorError):
        codec.decode("not-base64")
