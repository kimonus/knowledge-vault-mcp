import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
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


def _as_utc(value: datetime) -> datetime:
    """Interpret a timestamp without an offset as UTC so comparisons and storage are uniform."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _no_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("text must not contain NUL characters")
    return value


Topic = Annotated[str, Field(min_length=1, max_length=80), AfterValidator(_topic)]
Timestamp = Annotated[datetime, AfterValidator(_as_utc)]
SafeText = Annotated[str, AfterValidator(_no_nul)]


class SourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    title: Annotated[SafeText, Field(max_length=500)] | None = None
    publisher: Annotated[SafeText, Field(max_length=200)] | None = None
    retrieved_at: Timestamp | None = None

    @model_validator(mode="after")
    def reject_embedded_credentials(self) -> "SourceInput":
        if self.url.username or self.url.password:
            raise ValueError("source URL must not contain embedded credentials")
        for value in (str(self.url), self.title, self.publisher):
            secret = detect_secret(value) if value else None
            if secret:
                raise ValueError(f"suspected {secret} in source; store only a reference")
        return self


# Text only: the content is stored and returned as a string. Binary types need a storage kind
# of their own and an upload path that does not pass through tool arguments.
_TEXT_MEDIA_TYPE = re.compile(
    r"^(?:text/[a-z0-9][a-z0-9.+-]*"
    r"|application/(?:json|x-ndjson|yaml|x-yaml|xml|toml|sql|csv|javascript|x-sh"
    r"|[a-z0-9][a-z0-9.-]*\+(?:json|xml|yaml)))$"
)


class ArtifactBeginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: Annotated[SafeText, Field(min_length=1, max_length=255)]
    media_type: Annotated[str, Field(min_length=3, max_length=100)]
    declared_chunks: int = Field(ge=1)
    description: Annotated[SafeText, Field(max_length=1000)] | None = None

    @model_validator(mode="after")
    def validate_metadata(self) -> "ArtifactBeginInput":
        if self.filename in {".", ".."} or any(
            character in "/\\" or ord(character) < 32 for character in self.filename
        ):
            raise ValueError("filename must be a plain name without path separators")
        self.media_type = self.media_type.strip().lower()
        if not _TEXT_MEDIA_TYPE.fullmatch(self.media_type):
            raise ValueError("media_type must be a text type such as text/csv or application/json")
        for value in (self.filename, self.description):
            secret = detect_secret(value) if value else None
            if secret:
                raise ValueError(f"suspected {secret}; store only a reference to the secret")
        return self


class AssertionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: Annotated[SafeText, Field(min_length=1, max_length=16_000)]
    kind: AssertionKind
    origin: AssertionOrigin
    status: AssertionStatus = AssertionStatus.CURRENT
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    topics: list[Topic] = Field(default_factory=list, max_length=32)
    sources: list[SourceInput] = Field(default_factory=list, max_length=16)
    valid_from: Timestamp | None = None
    valid_to: Timestamp | None = None
    observed_at: Timestamp | None = None
    sensitivity: Sensitivity = Sensitivity.NORMAL
    supersedes_id: UUID | None = None
    # IDs returned by commit_knowledge_artifact; the assertion describes those artifacts.
    artifact_ids: list[UUID] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_semantics(self) -> "AssertionInput":
        for value in (self.content, *self.topics):
            secret = detect_secret(value)
            if secret:
                raise ValueError(f"suspected {secret}; store only a reference to the secret")
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be later than valid_from")
        self.topics = list(dict.fromkeys(self.topics))
        # One provenance row per URL: a repeated URL would violate the database constraint.
        unique_sources: dict[str, SourceInput] = {}
        for source in self.sources:
            unique_sources.setdefault(str(source.url), source)
        self.sources = list(unique_sources.values())
        self.artifact_ids = list(dict.fromkeys(self.artifact_ids))
        if self.status is AssertionStatus.SUPERSEDED and self.supersedes_id:
            raise ValueError("a new correction cannot itself be submitted as superseded")
        return self

    @property
    def normalized_content(self) -> str:
        return normalize_content(self.content)

    @property
    def stable_hash(self) -> str:
        return content_hash(self.normalized_content)


class SourceView(BaseModel):
    url: str
    title: str | None = None
    publisher: str | None = None
    retrieved_at: datetime | None = None


class ArtifactRef(BaseModel):
    """What an assertion shows of a linked artifact; the content is read separately."""

    id: UUID
    filename: str
    media_type: str
    size_bytes: int
    description: str | None = None


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
    sources: list[SourceView] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)


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


class CommitItem(BaseModel):
    """What the commit did with one accepted item. Holds no assertion content."""

    # Position among all submitted items, as in `rejected_items`.
    index: int
    assertion_id: UUID
    outcome: Literal["inserted", "confirmed_existing", "enriched_updated"]
    superseded_id: UUID | None = None
    conflict_ids: list[UUID] = Field(default_factory=list)
    # Submitted fields that differ from the existing record and were not applied to it.
    ignored_fields: list[str] = Field(default_factory=list)


class CommitResult(BaseModel):
    batch_id: UUID
    counts: CommitCounts
    assertion_ids: list[UUID] = Field(default_factory=list)
    # Empty in results that were stored before per-item outcomes existed.
    items: list[CommitItem] = Field(default_factory=list)
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
    valid_at: Timestamp | None = None
    created_after: Timestamp | None = None
    created_before: Timestamp | None = None


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
