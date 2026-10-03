# pyright: reportMissingImports=false
import asyncio
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

from knowledge_vault.embeddings.base import EmbeddingsUnavailableError


class SentenceTransformerProvider:
    """Lazy local provider; model loading occurs only on the first embedding call.

    Weights are read from local files (a filesystem path or a pre-populated Hugging Face cache)
    and never fetched at runtime unless `allow_download` is set for local development.

    One process holds one copy of the model: concurrent first calls wait for a single load, and
    encoding runs one call at a time, so memory and CPU use do not grow with request concurrency.
    """

    def __init__(
        self,
        model: str,
        dimensions: int,
        *,
        allow_download: bool = False,
        retry_after_seconds: float = 60.0,
    ) -> None:
        self._model_id = model
        self._dimensions = dimensions
        self._allow_download = allow_download
        self._retry_after_seconds = retry_after_seconds
        self._instance: Any | None = None
        self._unavailable_until = 0.0
        self._last_embed_failed = False
        self._load_lock = asyncio.Lock()
        self._encode_lock = asyncio.Lock()

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def degraded(self) -> bool:
        """True while loading is being backed off or the most recent embedding call failed."""
        if self._instance is None:
            return time.monotonic() < self._unavailable_until
        return self._last_embed_failed

    def _load(self) -> Any:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingsUnavailableError(
                "sentence-transformers is not installed; install the embeddings extra"
            ) from exc
        local_only = not self._allow_download or Path(self._model_id).exists()
        try:
            self._instance = SentenceTransformer(
                self._model_id,
                local_files_only=local_only,
            )
        except Exception as exc:
            raise EmbeddingsUnavailableError("local embedding model could not be loaded") from exc
        return self._instance

    async def _model(self) -> Any:
        if self._instance is not None:
            return self._instance
        # Requests that arrive while the model is loading wait for that load instead of each
        # starting their own; every copy costs more than a gigabyte.
        async with self._load_lock:
            if self._instance is not None:
                return self._instance
            if time.monotonic() < self._unavailable_until:
                # Fail fast so callers fall back to text search instead of reloading per call.
                raise EmbeddingsUnavailableError("local embedding model is unavailable")
            try:
                return await asyncio.to_thread(self._load)
            except EmbeddingsUnavailableError:
                self._unavailable_until = time.monotonic() + self._retry_after_seconds
                raise

    async def embed(self, texts: list[str]) -> list[list[float]]:
        model = await self._model()

        def encode() -> list[list[float]]:
            values = model.encode(texts, normalize_embeddings=True)
            return [list(map(float, vector)) for vector in values]

        async with self._encode_lock:
            # A loaded model can still fail every call (for example a dimension mismatch between
            # the model and the configuration); report that through `degraded` as well.
            self._last_embed_failed = True
            vectors = await asyncio.to_thread(encode)
            if any(len(vector) != self._dimensions for vector in vectors):
                raise EmbeddingsUnavailableError("embedding model returned unexpected dimensions")
            self._last_embed_failed = False
            return vectors


class DeterministicFakeProvider:
    """No-network deterministic provider for tests."""

    def __init__(self, dimensions: int = 8, model_id: str = "fake-v1") -> None:
        self._dimensions = dimensions
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        output: list[list[float]] = []
        for text in texts:
            digest = sha256(text.encode()).digest()
            vector = [
                ((digest[index % len(digest)] / 255.0) * 2.0) - 1.0
                for index in range(self._dimensions)
            ]
            norm = sum(value * value for value in vector) ** 0.5 or 1.0
            output.append([value / norm for value in vector])
        return output
