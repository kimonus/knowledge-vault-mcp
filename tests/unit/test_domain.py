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


F = "FAKE"


@pytest.mark.parametrize(
    "value",
    [
        f"DB is postgresql://app:{F}Passw0rd9x@db.internal:5432/app",
        f"mongodb+srv://svc:{F}hunter2hunter2@cluster0.example.net/db",
        f"clone https://deploy:{F}tok3nvalue99@git.example.org/repo.git",
        f"api_key={F}0123456789abcdef0123456789abcdef",
        f"token: {F}0123456789abcdef0123456789abcdef",
        f"client_secret={F}0123456789abcdef0123456789abcdef",
        f"aws_secret_access_key = {F}0123456789abcdefghijklmnopqrstuvwxyz",
        f"hasło: {F}-Correct-Horse-9",
        f"пароль: {F}-Correct-Horse-9",
        "xox" + "b-1234567890-1234567890123-" + F + "abcdefghijklmnopqrst",
        "AIza" + F + "0123456789abcdefghijklmnopqrstu",
        "sk_live_" + F + "0123456789abcdefghijklmn",
        "github_pat_" + F + "0123456789abcdefghijklmnopqrstuvwxyz0123456789",
        "glpat-" + F + "0123456789abcdefghij",
        "Cookie: session=" + F + "0123456789abcdef0123456789abcdef; Path=/",
        "tunnel eyJhIjoi" + F + "MTIzNDU2Nzg5MGFiY2RlZiIsInQiOiJhYmNkZWYiLCJzIjoiYWJjZGVmIn0=",
        "-----BEGIN PGP PRIVATE KEY BLOCK-----",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "kv_" + F + "0123456789abcdefghijklmnopqrstuvwxyzABCDE",
        "see https://example.org/doc?access_token=" + F + "0123456789abcdef",
        # Compatibility and invisible characters must not hide an otherwise detected value.
        "ｐａｓｓｗｏｒｄ＝" + F + "CorrectHorse9",
        "pass​word=" + F + "CorrectHorse9",
        "sk-​" + F + "abcdefghijklmnopqrstuvwxyz123456",
    ],
)
def test_common_secret_shapes_are_rejected(value: str) -> None:
    assert detect_secret(value) is not None
    with pytest.raises(ValidationError, match="suspected"):
        assertion(content=value)


@pytest.mark.parametrize(
    "value",
    [
        "The credential is stored in Kubernetes Secret vault-auth",
        "The API token lives in the password manager entry named homelab",
        "Rotate the tunnel token every quarter",
        "Docs are at https://example.org:8443/guide?page=2",
        "The secret to good bread is time",
    ],
)
def test_references_to_secrets_are_not_secrets(value: str) -> None:
    assert detect_secret(value) is None
    assert assertion(content=value).content == value


@pytest.mark.parametrize(
    "overrides",
    [
        {"sources": [{"url": f"https://user:{F}Passw0rd9x@example.org/doc"}]},
        {"sources": [{"url": f"https://example.org/doc?access_token={F}0123456789abcdef"}]},
        {
            "sources": [
                {"url": "https://example.org/", "title": "sk-" + F + "abcdefghijklmnopqrstuvwxyz1"}
            ]
        },
        {"sources": [{"url": "https://example.org/", "publisher": f"password={F}CorrectHorse9"}]},
        {"topics": ["sk-" + F + "abcdefghijklmnopqrstuvwxyz123456"]},
        {"sources": [{"url": "file:///etc/passwd"}]},
        {"sources": [{"url": "https://example.org/", "title": "nul\x00byte"}]},
        {"content": "nul\x00byte"},
    ],
)
def test_secret_shapes_and_nul_are_rejected_in_every_text_field(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        assertion(**overrides)


def test_timestamps_without_an_offset_are_treated_as_utc() -> None:
    parsed = assertion(valid_from="2026-01-01T00:00:00", valid_to="2026-06-01T00:00:00+02:00")
    assert parsed.valid_from == datetime(2026, 1, 1, tzinfo=UTC)
    assert parsed.valid_to is not None and parsed.valid_to.utcoffset() == timedelta(hours=2)
    with pytest.raises(ValidationError, match="valid_to"):
        assertion(valid_from="2026-06-01T00:00:00Z", valid_to="2026-01-01T00:00:00")


def test_repeated_source_urls_are_collapsed_keeping_the_first() -> None:
    parsed = assertion(
        sources=[
            {"url": "https://example.org/a", "title": "first"},
            {"url": "https://example.org/b"},
            {"url": "https://example.org/a", "title": "second"},
        ]
    )
    assert [(str(source.url), source.title) for source in parsed.sources] == [
        ("https://example.org/a", "first"),
        ("https://example.org/b", None),
    ]
