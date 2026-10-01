import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress

import structlog

from knowledge_vault.observability.logging import describe_exception
from knowledge_vault.worker.jobs import EmbeddingWorker


async def run_worker_loop(
    worker: EmbeddingWorker,
    maintenance: Callable[[], Awaitable[None]],
    stopping: asyncio.Event,
    *,
    poll_seconds: float,
    maintenance_interval_seconds: float,
) -> None:
    """Embed queued assertions and run periodic staging maintenance until asked to stop.

    A failing iteration is logged without content and retried after the poll interval, so a
    database restart does not terminate the worker.
    """
    logger = structlog.get_logger()
    next_maintenance = 0.0
    try:
        while not stopping.is_set():
            processed = 0
            try:
                if time.monotonic() >= next_maintenance:
                    await maintenance()
                    next_maintenance = time.monotonic() + maintenance_interval_seconds
                processed = await worker.process_once()
            except Exception as exc:
                logger.error("worker_iteration_failed", **describe_exception(exc))
            if not processed:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stopping.wait(), timeout=poll_seconds)
    finally:
        try:
            released = await worker.release_claims()
        except Exception as exc:
            # The database may be the reason for the shutdown; leases expire on their own.
            logger.error("worker_shutdown_failed", **describe_exception(exc))
        else:
            logger.info("worker_shutdown", operation="release_claims", released=released)
