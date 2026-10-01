import sys
from types import SimpleNamespace
from typing import Any

import pytest

from knowledge_vault.api.middleware import (
    ObservabilityMiddleware,
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
    assert calls == [("not-a-local-path", False)]

    mismatched = SentenceTransformerProvider("not-a-local-path", dimensions=3)
    with pytest.raises(EmbeddingsUnavailableError, match="unexpected dimensions"):
        await mismatched.embed(["one"])


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
    assert _safe_route("/a/00000000-0000-0000-0000-000000000001") == "/a/{id}"
    assert _safe_route("/" + ("x" * 41)) == "/{value}"
