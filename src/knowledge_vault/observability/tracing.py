# pyright: reportMissingImports=false
from typing import Any

from opentelemetry import trace

TRACER_NAME = "knowledge_vault"


def configure_tracing(*, enabled: bool, service: str, version: str) -> Any | None:
    """Return the tracer for request spans, or None when tracing is disabled.

    Spans carry only the method, a fixed route label, the status code, and an exception type;
    never assertion content. Exporting them needs the OpenTelemetry SDK and OTLP exporter, which
    are deliberately not part of the locked dependency set (the SDK depends on a pre-release
    package). An image that enables tracing must add them; the exporter is configured through
    the standard `OTEL_EXPORTER_OTLP_*` environment variables.
    """
    if not enabled:
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        raise RuntimeError(
            "KNOWLEDGE_VAULT_OTEL_ENABLED=true requires opentelemetry-sdk and "
            "opentelemetry-exporter-otlp-proto-http to be installed in the image"
        ) from exc
    provider = TracerProvider(
        resource=Resource.create({"service.name": service, "service.version": version})
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    return trace.get_tracer(TRACER_NAME, version)
