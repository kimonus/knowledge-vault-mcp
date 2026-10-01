from typing import Protocol


class EmbeddingProvider(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class EmbeddingsUnavailableError(RuntimeError):
    pass
