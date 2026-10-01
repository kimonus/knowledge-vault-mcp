from uuid import UUID

from knowledge_vault.observability.logging import redact_value
from knowledge_vault.services.conflicts import looks_contradictory, polarity_key
from knowledge_vault.worker.jobs import retry_delay


def test_opposite_polarity_conflict_detection_is_multilingual() -> None:
    assert looks_contradictory("user likes tea", "user does not likes tea")
    assert looks_contradictory("użytkownik lubi kawę", "użytkownik nie lubi kawę")
    assert not looks_contradictory("user likes tea", "user likes coffee")
    assert polarity_key("never enabled") == ("enabled", True)


def test_retry_delay_is_bounded_deterministic_exponential() -> None:
    job_id = UUID("00000000-0000-0000-0000-000000000001")
    assert retry_delay(1, job_id) == retry_delay(1, job_id)
    assert retry_delay(2, job_id) >= retry_delay(1, job_id)
    assert retry_delay(20, job_id) <= 4320


def test_log_redaction_removes_sensitive_fields_and_bearer() -> None:
    value = {
        "content": "private assertion",
        "nested": {"authorization": "Bearer abcdefghijklmnopqrstuvwxyz"},
        "message": "failed with Bearer abcdefghijklmnopqrstuvwxyz",
        "safe": 3,
    }
    redacted = redact_value(value)
    assert redacted["content"] == "[REDACTED]"
    assert redacted["nested"]["authorization"] == "[REDACTED]"
    assert "abcdefghijklmnopqrstuvwxyz" not in redacted["message"]
    assert redacted["safe"] == 3
