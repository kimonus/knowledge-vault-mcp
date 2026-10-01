# pyright: reportMissingImports=false
import asyncio
from hashlib import sha256
from pathlib import Path
from typing import Any

from knowledge_vault.embeddings.base import EmbeddingsUnavailableError


class SentenceTransformerProvider:
    """Lazy local provider; model loading occurs only on the first embedding call."""

    def __init__(self, model: str, dimensions: int) -> None:
        self._model_id = model
        self._dimensions = dimensions
        self._instance: Any | None = None

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _load(self) -> Any:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingsUnavailableError(
                "sentence-transformers is not installed; install the embeddings extra"
            ) from exc
        local_only = Path(self._model_id).exists()
        try:
            self._instance = SentenceTransformer(
                self._model_id,
                local_files_only=local_only,
            )
        except Exception as exc:
            raise EmbeddingsUnavailableError("local embedding model could not be loaded") from exc
        return self._instance

    async def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._instance or await asyncio.to_thread(self._load)

        def encode() -> list[list[float]]:
            values = model.encode(texts, normalize_embeddings=True)
            return [list(map(float, vector)) for vector in values]

        vectors = await asyncio.to_thread(encode)
        if any(len(vector) != self._dimensions for vector in vectors):
            raise EmbeddingsUnavailableError("embedding model returned unexpected dimensions")
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
