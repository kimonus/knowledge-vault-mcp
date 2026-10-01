from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from knowledge_vault.domain.models import AssertionInput, SearchFilters


class BeginFlushRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=200)
    declared_parts: int
    declared_items: int


class AppendPartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assertions: list[dict[str, Any]]


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    limit: int = 20
    cursor: str | None = None


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=200)
    correction: AssertionInput


class ForgetPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assertion_ids: list[str] = Field(min_length=1, max_length=100)


class ForgetConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_token: str = Field(min_length=20, max_length=200)
