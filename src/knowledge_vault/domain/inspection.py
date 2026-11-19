"""Transport-independent inputs for non-recording, bounded inspection reads."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from knowledge_vault.domain.enums import AssertionStatus
from knowledge_vault.domain.models import SearchFilters, Timestamp
from knowledge_vault.domain.temporal import StateIdentity


class BrowseFilters(SearchFilters):
    statuses: list[AssertionStatus] = Field(default_factory=list, max_length=4)
    missing_topic: bool = False
    missing_context: bool = False
    missing_facet: bool = False

    @model_validator(mode="after")
    def missing_values(self) -> "BrowseFilters":
        if (
            (self.missing_topic and self.topics)
            or (self.missing_context and self.context_id)
            or (self.missing_facet and self.facet)
        ):
            raise ValueError("missing-field filters cannot be combined with values for that field")
        return self


class PageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=16000)


class BrowseRequest(PageRequest):
    mode: Literal["all", "lexical"] = "all"
    query: str | None = Field(default=None, min_length=1, max_length=2000)
    filters: BrowseFilters = Field(default_factory=BrowseFilters)

    @field_validator("query")
    @classmethod
    def query_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        if not value or "\x00" in value:
            raise ValueError("query must contain text and no NUL characters")
        return value

    @model_validator(mode="after")
    def mode_query(self) -> "BrowseRequest":
        if (self.mode == "lexical") != (self.query is not None):
            raise ValueError("only lexical enumeration requires query text")
        return self


class CatalogRequest(PageRequest):
    dimension: Literal["contexts", "topics", "facets"]
    filters: BrowseFilters = Field(default_factory=BrowseFilters)
    prefix: str | None = Field(default=None, max_length=80, pattern=r"^[^\x00]*$")
    unscoped: bool = False

    @model_validator(mode="after")
    def facet_scope(self) -> "CatalogRequest":
        if self.dimension == "facets" and not (self.filters.context_id or self.unscoped):
            raise ValueError("facet catalog requires a context or explicit unscoped selection")
        return self


class EvidenceRequest(PageRequest):
    identity: StateIdentity
    known_at: Timestamp | None = None


class RankedRequest(PageRequest):
    query: str = Field(min_length=1, max_length=2000, pattern=r"^[^\x00]*$")
    filters: SearchFilters = Field(default_factory=SearchFilters)


class InspectionContext(BaseModel):
    """Supplied by the authenticated adapter, never accepted from a client body."""

    principal: str
    audience: str


class CursorState(BaseModel):
    key: str | None = None
    evaluated_at: datetime
