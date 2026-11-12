import asyncio
import signal

import structlog
from prometheus_client import start_http_server

from knowledge_vault.config import get_settings
from knowledge_vault.container import build_container
from knowledge_vault.embeddings.providers import SentenceTransformerProvider
from knowledge_vault.observability.logging import configure_logging
from knowledge_vault.persistence.schema_guard import SchemaGuard
from knowledge_vault.services.operations import WORKER
from knowledge_vault.worker.jobs import EmbeddingWorker
from knowledge_vault.worker.runner import run_worker_loop


def _provider() -> SentenceTransformerProvider:
    settings = get_settings()
    return SentenceTransformerProvider(
        settings.embedding_model,
        settings.embedding_dimensions,
        allow_download=settings.embedding_allow_download,
    )


async def _worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, service=settings.service_name, version=settings.version)
    provider = _provider() if settings.embeddings_enabled else None
    container = build_container(settings, embedder=provider)
    worker = (
        EmbeddingWorker(container.database.sessions, settings, provider)
        if provider is not None
        else None
    )
    if settings.worker_metrics_port:
        start_http_server(settings.worker_metrics_port)
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, stopping.set)

    async def maintenance() -> None:
        await container.ingestion.expire_and_purge()
        await container.artifacts.purge()
        await container.administration.purge_confirmation_tokens()
        await container.maintenance.purge()
        # Lets the watchdog and the statistics tool tell that the worker loop is alive.
        await container.operations.beat(WORKER)

    try:
        schema_guard = (
            SchemaGuard(container.database.sessions) if settings.standalone_mode else None
        )
        await run_worker_loop(
            worker,
            maintenance,
            stopping,
            poll_seconds=settings.worker_poll_seconds,
            maintenance_interval_seconds=settings.maintenance_interval_seconds,
            schema_ready=schema_guard.ready if schema_guard else None,
        )
    finally:
        await container.database.close()


def run_worker() -> None:
    asyncio.run(_worker())


async def _reembed() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, service=settings.service_name, version=settings.version)
    provider = _provider()
    container = build_container(settings, embedder=provider)
    worker = EmbeddingWorker(container.database.sessions, settings, provider)
    count = await worker.enqueue_rebuild(settings.embedding_model)
    structlog.get_logger().info("reembed_enqueued", operation="reembed", count=count)
    await container.database.close()


def run_reembed() -> None:
    asyncio.run(_reembed())
