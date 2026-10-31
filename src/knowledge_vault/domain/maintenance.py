"""Bounded contracts for knowledge maintenance; independent of transport adapters."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    field_validator,
    model_validator,
)

from knowledge_vault.domain.enums import AssertionKind, AssertionOrigin, Sensitivity
from knowledge_vault.domain.secrets import detect_secret

ShortText = Annotated[str, Field(min_length=1, max_length=200)]
Purpose = Literal["normal", "maintenance"]
EvidenceState = Literal["unknown", "reported", "inferred", "verified"]
TemporalRole = Literal["unspecified", "ongoing", "historical"]
WorkflowState = Literal["proposed", "active", "completed", "cancelled", "unresolved", "resolved"]
RelationKind = Literal["part_of", "depends_on", "supports", "equivalent_to", "resolves"]


class SafeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def safe_strings(self) -> "SafeModel":
        def check(value: object) -> None:
            if isinstance(value, str) and ("\x00" in value or detect_secret(value)):
                raise ValueError("text contains a prohibited value; store only a reference")
            if isinstance(value, dict):
                for entry in value.values():
                    check(entry)
            if isinstance(value, list):
                for entry in value:
                    check(entry)

        check(self.model_dump(mode="json"))
        for name, value in self.__dict__.items():
            if isinstance(value, datetime) and value.tzinfo is None:
                setattr(self, name, value.replace(tzinfo=UTC))
        return self


class Provenance(SafeModel):
    kind: Literal["web", "conversation", "file", "artifact", "command", "unknown"]
    locator: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    title: Annotated[str, Field(max_length=200)] | None = None
    observed_at: datetime | None = None
    conversation_id: ShortText | None = None
    message_ids: list[ShortText] = Field(default_factory=list, max_length=16)
    timestamp_precision: Literal["instant", "day", "month", "year", "unknown"] = "unknown"
    timestamp_basis: Literal["message", "conversation", "source", "unknown"] = "unknown"
    timestamp_confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def locator_shape(self) -> "Provenance":
        if self.kind != "unknown" and not self.locator:
            raise ValueError("known provenance requires a locator")
        if self.kind == "web":
            url = TypeAdapter(HttpUrl).validate_python(self.locator)
            if url.username or url.password:
                raise ValueError("source URL must not contain credentials")
        if self.kind == "artifact":
            UUID(self.locator or "")
        return self


class KnowledgeMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_state: EvidenceState = "unknown"
    temporal_role: TemporalRole = "unspecified"
    workflow_state: WorkflowState | None = None
    last_verified_at: datetime | None = None
    review_after: datetime | None = None
    context_id: UUID | None = None
    facet: Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_.-]+$")] | None = (
        None
    )
    provenance: list[Provenance] = Field(default_factory=list, max_length=16)

    @field_validator("last_verified_at", "review_after")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


class MetadataChanges(KnowledgeMetadata):
    kind: AssertionKind | None = None
    origin: AssertionOrigin | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    sensitivity: Sensitivity | None = None
    status: Literal["current", "uncertain", "disputed"] | None = None
    observed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    topics: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        default_factory=list, max_length=32
    )
    # URL sources are replaced explicitly through this operation, never silently merged.
    sources: list[dict[str, object]] = Field(default_factory=list, max_length=16)


class MetadataUpdate(SafeModel):
    assertion_id: UUID
    expected_revision: int = Field(ge=1)
    idempotency_key: ShortText
    changes: MetadataChanges
    dry_run: bool = False


class ContextInput(SafeModel):
    id: UUID
    name: ShortText
    aliases: list[ShortText] = Field(default_factory=list, max_length=16)


class LinkInput(SafeModel):
    source_id: UUID
    target_id: UUID
    kind: RelationKind
    position: int | None = Field(default=None, ge=0, le=9999)

    @model_validator(mode="after")
    def shape(self) -> "LinkInput":
        if self.source_id == self.target_id:
            raise ValueError("a relationship must have different endpoints")
        if self.position is not None and self.kind != "part_of":
            raise ValueError("only part_of accepts a position")
        return self


class TopicAliasInput(SafeModel):
    alias: Annotated[str, Field(min_length=1, max_length=80)]
    canonical: Annotated[str, Field(min_length=1, max_length=80)]


class UsageReport(SafeModel):
    assertion_ids: list[UUID] = Field(min_length=1, max_length=100)
    event_id: UUID


class UsageView(BaseModel):
    available: bool = True
    last_retrieved_at: datetime | None = None
    retrieval_count: int = 0
    last_explicit_fetch_at: datetime | None = None
    explicit_fetch_count: int = 0
    last_used_at: datetime | None = None
    use_count: int = 0
