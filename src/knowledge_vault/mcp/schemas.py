from typing import Any

from pydantic import BaseModel


class CompatibilitySearchResult(BaseModel):
    id: str
    title: str
    url: str


class CompatibilitySearchOutput(BaseModel):
    results: list[CompatibilitySearchResult]


class CompatibilityFetchOutput(BaseModel):
    id: str
    title: str
    text: str
    url: str
    metadata: dict[str, Any] | None = None
