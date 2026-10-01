import uvicorn

from knowledge_vault.api.app import create_app
from knowledge_vault.config import get_settings
from knowledge_vault.container import build_container
from knowledge_vault.observability.logging import configure_logging
from knowledge_vault.observability.tracing import configure_tracing


def application():
    settings = get_settings()
    configure_logging(settings.log_level, service=settings.service_name, version=settings.version)
    tracer = configure_tracing(
        enabled=settings.otel_enabled, service=settings.service_name, version=settings.version
    )
    return create_app(build_container(settings, tracer=tracer))


app = application()


def run_api() -> None:
    uvicorn.run(
        "knowledge_vault.main:app",
        host="0.0.0.0",  # noqa: S104  # nosec B104
        port=8000,
        log_config=None,
        # Requests are logged once, as structured events without query strings, by the
        # observability middleware.
        access_log=False,
        proxy_headers=False,
    )
