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


def test_exception_descriptions_never_include_the_message() -> None:
    from knowledge_vault.observability.logging import describe_exception

    class DriverError(Exception):
        sqlstate = "23505"

    class Wrapped(Exception):
        def __init__(self, orig: Exception) -> None:
            super().__init__("statement failed with parameters ('private assertion text',)")
            self.orig = orig

    try:
        try:
            raise DriverError("Key (url)=(https://private.example/secret-path) already exists")
        except DriverError as driver_error:
            raise Wrapped(driver_error) from driver_error
    except Wrapped as exc:
        described = describe_exception(exc)

    assert described["error_type"].endswith("Wrapped")
    assert described["error_cause"].endswith("DriverError")
    assert described["sqlstate"] == "23505"
    assert described["error_frames"][-1].startswith("test_conflicts_worker_logging.py:")
    assert "private" not in str(described)


async def test_worker_loop_shuts_down_cleanly_when_the_database_is_unreachable() -> None:
    import asyncio

    from knowledge_vault.worker.runner import run_worker_loop

    class Unreachable:
        async def process_once(self) -> int:
            raise ConnectionError("connection refused")

        async def release_claims(self) -> int:
            raise ConnectionError("connection refused")

    stopping = asyncio.Event()
    calls = 0

    async def maintenance() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            stopping.set()

    await asyncio.wait_for(
        run_worker_loop(
            Unreachable(),  # type: ignore[arg-type]
            maintenance,
            stopping,
            poll_seconds=0.01,
            maintenance_interval_seconds=0.0,
        ),
        timeout=5,
    )
    assert calls == 2
