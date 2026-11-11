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


async def test_worker_loop_runs_maintenance_without_an_embedding_worker() -> None:
    import asyncio

    from knowledge_vault.worker.runner import run_worker_loop

    stopping = asyncio.Event()
    calls = 0

    async def maintenance() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            stopping.set()

    await asyncio.wait_for(
        run_worker_loop(
            None, maintenance, stopping, poll_seconds=0.01, maintenance_interval_seconds=0.0
        ),
        timeout=5,
    )
    assert calls == 2


async def test_maintenance_only_worker_retries_failed_maintenance() -> None:
    import asyncio

    from knowledge_vault.worker.runner import run_worker_loop

    stopping = asyncio.Event()
    calls = 0

    async def maintenance() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("transient database failure")
        stopping.set()

    await asyncio.wait_for(
        run_worker_loop(
            None, maintenance, stopping, poll_seconds=0.01, maintenance_interval_seconds=0.0
        ),
        timeout=5,
    )
    assert calls == 2


async def test_disabled_embeddings_worker_runs_all_maintenance_without_provider(
    monkeypatch,
) -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from knowledge_vault.config import Settings
    from knowledge_vault.services.operations import WORKER
    from knowledge_vault.worker import main

    settings = Settings(environment="test", embeddings_enabled=False, worker_metrics_port=0)
    container = SimpleNamespace(
        database=SimpleNamespace(close=AsyncMock()),
        ingestion=SimpleNamespace(expire_and_purge=AsyncMock()),
        artifacts=SimpleNamespace(purge=AsyncMock()),
        administration=SimpleNamespace(purge_confirmation_tokens=AsyncMock()),
        maintenance=SimpleNamespace(purge=AsyncMock()),
        operations=SimpleNamespace(beat=AsyncMock()),
    )
    build = Mock(return_value=container)
    provider = Mock(side_effect=AssertionError("disabled worker must not construct provider"))
    embedding_worker = Mock(side_effect=AssertionError("disabled worker must not claim jobs"))
    container.artifacts.purge.side_effect = [ConnectionError("temporary failure"), None]

    async def run(worker, maintenance, stopping, **kwargs) -> None:
        assert worker is None
        try:
            await maintenance()
        except ConnectionError:
            pass
        else:
            raise AssertionError("failed duty must propagate")
        container.operations.beat.assert_not_awaited()
        await maintenance()

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "configure_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "build_container", build)
    monkeypatch.setattr(main, "_provider", provider)
    monkeypatch.setattr(main, "EmbeddingWorker", embedding_worker)
    monkeypatch.setattr(main, "run_worker_loop", run)
    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", lambda *args: None)
    await main._worker()
    build.assert_called_once_with(settings, embedder=None)
    provider.assert_not_called()
    embedding_worker.assert_not_called()
    assert container.ingestion.expire_and_purge.await_count == 2
    assert container.artifacts.purge.await_count == 2
    container.administration.purge_confirmation_tokens.assert_awaited_once()
    container.maintenance.purge.assert_awaited_once()
    container.operations.beat.assert_awaited_once_with(WORKER)
    container.database.close.assert_awaited_once()
