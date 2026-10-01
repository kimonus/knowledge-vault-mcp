from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, HttpUrl, model_validator

from knowledge_vault.domain.enums import (
    AssertionKind,
    AssertionOrigin,
    AssertionStatus,
    EmbeddingState,
    Sensitivity,
)
from knowledge_vault.domain.normalization import content_hash, normalize_content, normalize_topic
from knowledge_vault.domain.secrets import detect_secret


def _topic(value: str) -> str:
    normalized = normalize_topic(value)
    if not normalized:
        raise ValueError("topic is empty after normalization")
    return normalized


Topic = Annotated[str, Field(min_length=1, max_length=80), AfterValidator(_topic)]


class SourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    title: str | None = Field(default=None, max_length=500)
    publisher: str | None = Field(default=None, max_length=200)
    retrieved_at: datetime | None = None


class AssertionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=16_000)
    kind: AssertionKind
    origin: AssertionOrigin
    status: AssertionStatus = AssertionStatus.CURRENT
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    topics: list[Topic] = Field(default_factory=list, max_length=32)
    sources: list[SourceInput] = Field(default_factory=list, max_length=16)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    observed_at: datetime | None = None
    sensitivity: Sensitivity = Sensitivity.NORMAL
    supersedes_id: UUID | None = None

    @model_validator(mode="after")
    def validate_semantics(self) -> "AssertionInput":
        secret = detect_secret(self.content)
        if secret:
            raise ValueError(f"suspected {secret}; store only a reference to the secret")
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be later than valid_from")
        self.topics = list(dict.fromkeys(self.topics))
        if self.status is AssertionStatus.SUPERSEDED and self.supersedes_id:
            raise ValueError("a new correction cannot itself be submitted as superseded")
        return self

    @property
    def normalized_content(self) -> str:
        return normalize_content(self.content)

    @property
    def stable_hash(self) -> str:
        return content_hash(self.normalized_content)


class AssertionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    content: str
    kind: AssertionKind
    origin: AssertionOrigin
    status: AssertionStatus
    confidence: float
    topics: list[str]
    sensitivity: Sensitivity
    valid_from: datetime | None
    valid_to: datetime | None
    observed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_confirmed_at: datetime
    confirmation_count: int
    supersedes_id: UUID | None
    embedding_state: EmbeddingState


class RejectedItem(BaseModel):
    index: int
    code: str
    message: str


class CommitCounts(BaseModel):
    inserted: int = 0
    confirmed_existing: int = 0
    enriched_updated: int = 0
    superseded: int = 0
    possible_conflicts: int = 0
    rejected: int = 0
    embedding_pending: int = 0


class CommitResult(BaseModel):
    batch_id: UUID
    counts: CommitCounts
    assertion_ids: list[UUID] = Field(default_factory=list)
    conflict_ids: list[UUID] = Field(default_factory=list)
    rejected_items: list[RejectedItem] = Field(default_factory=list)


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kinds: list[AssertionKind] = Field(default_factory=list, max_length=12)
    origins: list[AssertionOrigin] = Field(default_factory=list, max_length=5)
    statuses: list[AssertionStatus] = Field(
        default_factory=lambda: [AssertionStatus.CURRENT], max_length=4
    )
    topics: list[Topic] = Field(default_factory=list, max_length=16)
    sensitivities: list[Sensitivity] = Field(default_factory=list, max_length=3)
    minimum_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    valid_at: datetime | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None


class SearchHit(BaseModel):
    assertion: AssertionView
    score: float = Field(ge=0.0)


class SearchPage(BaseModel):
    results: list[SearchHit]
    next_cursor: str | None = None
    embedding_degraded: bool = False


class ProblemDetail(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str
    instance: str | None = None
    errors: list[dict[str, Any]] | None = None
