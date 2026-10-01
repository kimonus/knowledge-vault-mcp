import sys
from types import SimpleNamespace
from typing import Any

import pytest

from knowledge_vault.api.middleware import (
    ObservabilityMiddleware,
    OriginValidationMiddleware,
    RequestSizeLimitMiddleware,
    _safe_route,
)
from knowledge_vault.embeddings.base import EmbeddingsUnavailableError
from knowledge_vault.embeddings.providers import (
    DeterministicFakeProvider,
    SentenceTransformerProvider,
)


async def test_fake_provider_is_normalized_and_deterministic() -> None:
    provider = DeterministicFakeProvider(dimensions=8, model_id="fake-test")
    assert provider.model_id == "fake-test"
    assert provider.dimensions == 8
    first = await provider.embed(["zażółć", "zażółć"])
    assert first[0] == first[1]
    assert len(first[0]) == 8
    assert sum(value * value for value in first[0]) == pytest.approx(1.0)


async def test_sentence_transformer_provider_lazy_success_and_dimension_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []

    class Model:
        def __init__(self, model_id: str, *, local_files_only: bool) -> None:
            calls.append((model_id, local_files_only))

        def encode(self, texts: list[str], *, normalize_embeddings: bool) -> list[list[float]]:
            assert normalize_embeddings
            return [[float(index), 1.0] for index, _ in enumerate(texts)]

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=Model)
    )
    provider = SentenceTransformerProvider("not-a-local-path", dimensions=2)
    assert provider.model_id == "not-a-local-path"
    assert provider.dimensions == 2
    assert await provider.embed(["one", "two"]) == [[0.0, 1.0], [1.0, 1.0]]
    # Weights are loaded from local files only unless a developer opts in to downloading.
    assert calls == [("not-a-local-path", True)]
    downloading = SentenceTransformerProvider("not-a-local-path", dimensions=2, allow_download=True)
    await downloading.embed(["one"])
    assert calls[-1] == ("not-a-local-path", False)

    assert provider.degraded is False
    mismatched = SentenceTransformerProvider("not-a-local-path", dimensions=3)
    with pytest.raises(EmbeddingsUnavailableError, match="unexpected dimensions"):
        await mismatched.embed(["one"])
    # The model loaded, but a call that cannot succeed is still reported as degraded.
    assert mismatched.degraded is True


async def test_sentence_transformer_provider_wraps_load_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenModel:
        def __init__(self, model_id: str, *, local_files_only: bool) -> None:
            del model_id, local_files_only
            raise RuntimeError("model detail that must not escape")

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=BrokenModel),
    )
    provider = SentenceTransformerProvider("broken", dimensions=2)
    with pytest.raises(EmbeddingsUnavailableError, match="could not be loaded"):
        await provider.embed(["private text"])


async def test_size_limit_handles_non_http_declared_and_streamed_bodies() -> None:
    sent: list[dict[str, Any]] = []
    delegated: list[str] = []

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        delegated.append(scope["type"])
        if scope["type"] == "http":
            await receive()
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def empty_receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    middleware = RequestSizeLimitMiddleware(app, max_bytes=4)
    await middleware({"type": "lifespan"}, empty_receive, send)  # type: ignore[arg-type]
    assert delegated == ["lifespan"]

    sent.clear()
    await middleware(
        {"type": "http", "headers": [(b"content-length", b"5")]},  # type: ignore[arg-type]
        empty_receive,
        send,
    )
    assert sent[0]["status"] == 413

    sent.clear()

    async def large_receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"12345", "more_body": False}

    await middleware({"type": "http", "headers": []}, large_receive, send)  # type: ignore[arg-type]
    assert sent[0]["status"] == 413


async def test_observability_delegates_non_http_and_route_is_bounded() -> None:
    delegated: list[str] = []

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del receive, send
        delegated.append(scope["type"])

    async def receive() -> dict[str, Any]:
        return {"type": "lifespan.startup"}

    async def send(message: dict[str, Any]) -> None:
        del message

    await ObservabilityMiddleware(app)({"type": "lifespan"}, receive, send)  # type: ignore[arg-type]
    assert delegated == ["lifespan"]
    assert (
        _safe_route("/api/v1/assertions/00000000-0000-0000-0000-000000000001")
        == "/api/v1/assertions/{id}"
    )
    assert _safe_route("/api/v1/flushes/abc/parts/7") == "/api/v1/flushes/{id}/parts/{n}"
    assert _safe_route("/mcp/") == "/mcp"
    assert _safe_route("/.well-known/oauth-protected-resource/mcp") == "/.well-known/{document}"
    # Arbitrary caller-chosen paths collapse into one label and cannot mint metric series.
    assert _safe_route("/" + ("x" * 41)) == "{unmatched}"
    assert _safe_route("/probe-1/2") == _safe_route("/probe-3/4") == "{unmatched}"


async def test_provider_backs_off_after_a_failed_load(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    class BrokenModel:
        def __init__(self, model_id: str, *, local_files_only: bool) -> None:
            nonlocal attempts
            del model_id, local_files_only
            attempts += 1
            raise OSError("weights are not available locally")

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=BrokenModel)
    )
    provider = SentenceTransformerProvider("missing", dimensions=2, retry_after_seconds=60)
    assert provider.degraded is False
    for _ in range(5):
        with pytest.raises(EmbeddingsUnavailableError):
            await provider.embed(["query"])
    # Only the first call paid for a load attempt; the rest failed fast.
    assert attempts == 1
    assert provider.degraded is True

    eager = SentenceTransformerProvider("missing", dimensions=2, retry_after_seconds=0)
    for _ in range(2):
        with pytest.raises(EmbeddingsUnavailableError):
            await eager.embed(["query"])
    assert attempts == 3


async def _run(app: Any, scope: dict[str, Any], body: bytes = b"") -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


async def test_size_limit_overrides_an_application_error_for_the_aborted_read() -> None:
    async def framework_like_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del scope
        try:
            await receive()
        except Exception:
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": b"could not parse"})

    middleware = RequestSizeLimitMiddleware(framework_like_app, max_bytes=4)
    sent = await _run(middleware, {"type": "http", "headers": []}, b"12345")
    assert [message["status"] for message in sent if "status" in message] == [413]
    # A non-numeric Content-Length is left to the server and application, not a crash here.
    assert (
        await _run(
            middleware, {"type": "http", "headers": [(b"content-length", b"not-a-number")]}, b"1"
        )
        == []
    )


async def test_origin_validation_allows_native_clients_and_configured_origins() -> None:
    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del scope, receive
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = OriginValidationMiddleware(
        app, allowed_origins=frozenset({"https://Tools.Example.com/"})
    )

    async def status(path: str, *origins: bytes) -> int:
        headers = [(b"origin", origin) for origin in origins]
        return (await _run(middleware, {"type": "http", "path": path, "headers": headers}))[0][
            "status"
        ]

    assert await status("/mcp") == 204
    assert await status("/mcp", b"https://tools.example.com") == 204
    assert await status("/mcp", b"https://evil.example") == 403
    assert await status("/mcp/", b"null") == 403
    assert await status("/mcp", b"https://tools.example.com", b"https://evil.example") == 403
    assert await status("/api/v1/search", b"https://evil.example") == 204
    assert await _run(middleware, {"type": "lifespan"}) == [
        {"type": "http.response.start", "status": 204, "headers": []},
        {"type": "http.response.body", "body": b""},
    ]


async def test_observability_turns_unhandled_errors_into_content_free_responses() -> None:
    async def failing(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del scope, receive, send
        raise RuntimeError("private assertion text in an exception message")

    sent = await _run(
        ObservabilityMiddleware(failing),
        {"type": "http", "method": "POST", "path": "/api/v1/search", "headers": []},
    )
    assert sent[0]["status"] == 500
    assert any(name == b"x-request-id" for name, _ in sent[0]["headers"])
    assert b"private assertion text" not in sent[1]["body"]

    async def fails_after_start(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del scope, receive
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("stream broke")

    sent = await _run(
        ObservabilityMiddleware(fails_after_start),
        {"type": "http", "method": "GET", "path": "/mcp", "headers": []},
    )
    assert [message["type"] for message in sent] == ["http.response.start"]


class _RecordingSpan:
    def __init__(self, name: str, attributes: dict[str, Any]) -> None:
        self.name = name
        self.attributes = dict(attributes)
        self.status: Any = None

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_status(self, status: Any) -> None:
        self.status = status

    def __enter__(self) -> "_RecordingSpan":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


class _RecordingTracer:
    def __init__(self) -> None:
        self.spans: list[_RecordingSpan] = []

    def start_as_current_span(self, name: str, **options: Any) -> _RecordingSpan:
        span = _RecordingSpan(name, options["attributes"])
        self.spans.append(span)
        return span


async def test_request_spans_are_content_free_and_optional() -> None:
    async def failing(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del scope, receive, send
        raise RuntimeError("private assertion text in an exception message")

    async def healthy(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del scope, receive
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    tracer = _RecordingTracer()
    path = "/api/v1/assertions/00000000-0000-0000-0000-000000000001"
    await _run(
        ObservabilityMiddleware(healthy, tracer=tracer),
        {"type": "http", "method": "GET", "path": path, "headers": []},
    )
    await _run(
        ObservabilityMiddleware(failing, tracer=tracer),
        {"type": "http", "method": "POST", "path": "/secret-looking-path", "headers": []},
    )
    ok, failed = tracer.spans
    assert ok.name == "GET /api/v1/assertions/{id}"
    assert ok.attributes == {
        "http.request.method": "GET",
        "http.route": "/api/v1/assertions/{id}",
        "http.response.status_code": 200,
    }
    assert failed.name == "POST {unmatched}"
    assert failed.attributes["error.type"] == "RuntimeError"
    assert failed.attributes["http.response.status_code"] == 500
    assert failed.status is not None
    assert "private" not in str(failed.attributes) and "secret-looking" not in failed.name


def test_tracing_is_off_by_default_and_fails_closed_without_the_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from knowledge_vault.observability.tracing import configure_tracing

    assert configure_tracing(enabled=False, service="s", version="v") is None
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk.trace", None)
    with pytest.raises(RuntimeError, match="requires opentelemetry-sdk"):
        configure_tracing(enabled=True, service="s", version="v")


def test_tracing_configures_an_exporting_provider_when_the_sdk_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opentelemetry import trace

    from knowledge_vault.observability import tracing

    created: dict[str, Any] = {}

    class Provider:
        def __init__(self, resource: Any) -> None:
            created["resource"] = resource
            self.processors: list[Any] = []

        def add_span_processor(self, processor: Any) -> None:
            self.processors.append(processor)

    modules = {
        "opentelemetry.exporter.otlp.proto.http.trace_exporter": SimpleNamespace(
            OTLPSpanExporter=lambda: "exporter"
        ),
        "opentelemetry.sdk.resources": SimpleNamespace(
            Resource=SimpleNamespace(create=lambda attributes: attributes)
        ),
        "opentelemetry.sdk.trace": SimpleNamespace(TracerProvider=Provider),
        "opentelemetry.sdk.trace.export": SimpleNamespace(
            BatchSpanProcessor=lambda exporter: ("batch", exporter)
        ),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    installed: list[Any] = []
    monkeypatch.setattr(trace, "set_tracer_provider", installed.append)

    tracer = tracing.configure_tracing(enabled=True, service="vault", version="1.2.3")
    assert tracer is not None
    assert created["resource"] == {"service.name": "vault", "service.version": "1.2.3"}
    assert installed[0].processors == [("batch", "exporter")]
