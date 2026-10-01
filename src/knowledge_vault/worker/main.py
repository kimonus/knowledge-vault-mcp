import asyncio
import signal
from contextlib import suppress

import structlog

from knowledge_vault.config import get_settings
from knowledge_vault.container import build_container
from knowledge_vault.embeddings.providers import SentenceTransformerProvider
from knowledge_vault.observability.logging import configure_logging
from knowledge_vault.worker.jobs import EmbeddingWorker


async def _worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    provider = SentenceTransformerProvider(settings.embedding_model, settings.embedding_dimensions)
    container = build_container(settings, embedder=provider)
    worker = EmbeddingWorker(container.database.sessions, settings, provider)
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, stopping.set)
    logger = structlog.get_logger()
    try:
        while not stopping.is_set():
            processed = await worker.process_once()
            if not processed:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stopping.wait(), timeout=settings.worker_poll_seconds)
    finally:
        released = await worker.release_claims()
        logger.info("worker_shutdown", operation="release_claims", released=released)
        await container.database.close()


def run_worker() -> None:
    asyncio.run(_worker())


async def _reembed() -> None:
    settings = get_settings()
    provider = SentenceTransformerProvider(settings.embedding_model, settings.embedding_dimensions)
    container = build_container(settings, embedder=provider)
    worker = EmbeddingWorker(container.database.sessions, settings, provider)
    count = await worker.enqueue_rebuild(settings.embedding_model)
    structlog.get_logger().info("reembed_enqueued", operation="reembed", count=count)
    await container.database.close()


def run_reembed() -> None:
    asyncio.run(_reembed())
