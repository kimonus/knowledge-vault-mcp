from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from knowledge_vault.domain.models import AssertionInput
from knowledge_vault.domain.normalization import content_hash, normalize_content, normalize_topic
from knowledge_vault.domain.secrets import detect_secret


def assertion(**overrides: object) -> AssertionInput:
    data: dict[str, object] = {
        "content": "The user prefers dark mode.",
        "kind": "preference",
        "origin": "user",
        "topics": [" UI ", "ui", "Personalization"],
    }
    data.update(overrides)
    return AssertionInput.model_validate(data)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Café\u00a0NOIR  ", "café noir"),
        ("Zażółć   GĘŚLĄ", "zażółć gęślą"),
        ("ПРИВЕТ\nмир", "привет мир"),
        ("ＡＢＣ", "abc"),
    ],
)
def test_normalize_content_multilingual(raw: str, expected: str) -> None:
    assert normalize_content(raw) == expected
    assert len(content_hash(expected)) == 64


def test_topics_are_normalized_and_deduplicated() -> None:
    item = assertion()
    assert item.topics == ["ui", "personalization"]
    assert normalize_topic("  Project / Alpha ") == "project-alpha"


def test_stable_hash_ignores_spacing_and_case() -> None:
    assert (
        assertion(content="HELLO   World").stable_hash
        == assertion(content="hello world").stable_hash
    )


def test_invalid_time_range_is_rejected() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="valid_to"):
        assertion(valid_from=now, valid_to=now - timedelta(seconds=1))


@pytest.mark.parametrize(
    ("value", "label"),
    [
        ("-----BEGIN PRIVATE KEY-----", "private key"),
        ("sk-abcdefghijklmnopqrstuvwxyz123456", "OpenAI API key"),
        ("password=correct-horse-battery-staple", "password assignment"),
        ("Bearer abcdefghijklmnopqrstuvwxyz1234", "bearer token"),
    ],
)
def test_secret_detection_and_rejection(value: str, label: str) -> None:
    assert detect_secret(value) == label
    with pytest.raises(ValidationError, match="suspected"):
        assertion(content=value)


def test_secret_reference_is_allowed() -> None:
    assert assertion(content="The credential is stored in Kubernetes Secret vault-auth").content


def test_confidence_and_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        assertion(confidence=1.01)
    with pytest.raises(ValidationError):
        assertion(unexpected="value")
