from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from knowledge_vault.domain.inspection import CursorState
from knowledge_vault.services.inspection_cursors import InspectionCursors


@given(st.text(max_size=200), st.text(max_size=100))
def test_opaque_cursor_keys_round_trip(key, binding):
    codec = InspectionCursors("property-synthetic-key")
    state = CursorState(evaluated_at=datetime.now(UTC))
    decoded = codec.start(codec.encode(binding, state, key), binding)
    assert decoded.key == key
    assert decoded.evaluated_at == state.evaluated_at
