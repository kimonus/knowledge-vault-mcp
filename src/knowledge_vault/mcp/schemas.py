from typing import Any

from pydantic import BaseModel, ConfigDict


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


class BeginFlushOutput(BaseModel):
    batch_id: str
    state: str
    declared_parts: int
    declared_items: int
    replayed: bool


class AppendOutput(BaseModel):
    batch_id: str
    part_number: int
    accepted: int
    rejected: list[dict[str, Any]]
    replayed: bool


class OperationOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    success: bool
