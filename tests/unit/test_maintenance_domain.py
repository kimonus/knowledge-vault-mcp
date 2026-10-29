import secrets
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from knowledge_vault.domain.maintenance import ContextInput, LinkInput, Provenance
from knowledge_vault.domain.models import AssertionInput


@pytest.mark.parametrize(
    "kind,locator",
    [
        ("web", "https://example.com/evidence"),
        ("conversation", "conversation:opaque-reference"),
        ("file", "document:section-2"),
        ("command", "observation:status"),
        ("artifact", str(uuid4())),
        ("unknown", None),
    ],
)
def test_typed_provenance(kind, locator):
    value = Provenance(kind=kind, locator=locator, observed_at=datetime(2026, 1, 1))
    assert value.observed_at.tzinfo == UTC


@pytest.mark.parametrize(
    "raw",
    [
        {"kind": "file"},
        {"kind": "web", "locator": "file:///tmp/test"},
        {"kind": "web", "locator": "https://user:password@example.com"},
        {"kind": "artifact", "locator": "not-a-uuid"},
        {"kind": "command", "locator": "password=" + secrets.token_urlsafe(24)},
        {"kind": "conversation", "locator": "bad\x00reference"},
    ],
)
def test_invalid_provenance(raw):
    with pytest.raises(ValidationError):
        Provenance(**raw)


def test_boundaries_and_evidence_semantics():
    raw = {
        "content": "Synthetic procedure with all needed steps.",
        "kind": "procedure",
        "origin": "assistant",
    }
    value = AssertionInput(**raw)
    assert value.evidence_state == "unknown" and value.confidence == 1.0
    with pytest.raises(ValidationError):
        AssertionInput(**raw, evidence_state="verified")
    with pytest.raises(ValidationError):
        AssertionInput(
            **raw,
            evidence_state="verified",
            last_verified_at=datetime.now(UTC),
            provenance=[{"kind": "unknown"}],
        )
    with pytest.raises(ValidationError):
        AssertionInput(**raw, workflow_state="completed")
    with pytest.raises(ValidationError):
        ContextInput(id=uuid4(), name="password=" + secrets.token_urlsafe(24))
    ident = uuid4()
    with pytest.raises(ValidationError):
        LinkInput(source_id=ident, target_id=ident, kind="part_of")
    with pytest.raises(ValidationError):
        LinkInput(source_id=ident, target_id=uuid4(), kind="supports", position=1)
