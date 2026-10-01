import unicodedata
from uuid import UUID

from hypothesis import given
from hypothesis import strategies as st

from knowledge_vault.domain.normalization import normalize_content
from knowledge_vault.services.cursors import Cursor, CursorCodec
from knowledge_vault.services.ranking import reciprocal_rank_fusion


@given(st.text(max_size=500))
def test_normalization_is_idempotent(value: str) -> None:
    assert normalize_content(normalize_content(value)) == normalize_content(value)
    assert unicodedata.normalize("NFKC", normalize_content(value)) == normalize_content(value)


@given(
    st.lists(st.integers(min_value=0, max_value=100), unique=True, max_size=30),
    st.lists(st.integers(min_value=0, max_value=100), unique=True, max_size=30),
)
def test_rank_fusion_is_stable_and_contains_union(text: list[int], vector: list[int]) -> None:
    first = reciprocal_rank_fusion(text, vector)
    second = reciprocal_rank_fusion(text, vector)
    assert first == second
    assert {item.item for item in first} == set(text) | set(vector)


@given(st.text(min_size=1, max_size=50), st.floats(min_value=0, max_value=1, allow_nan=False))
def test_cursor_round_trip(query_hash: str, score: float) -> None:
    codec = CursorCodec("property-test")
    cursor = Cursor(query_hash, score, UUID(int=1))
    assert codec.decode(codec.encode(cursor)) == cursor
