import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, deferred, mapped_column, relationship

from knowledge_vault.domain.enums import (
    ArtifactState,
    ArtifactStorage,
    AssertionKind,
    AssertionOrigin,
    AssertionStatus,
    BatchState,
    EmbeddingState,
    JobState,
    Sensitivity,
)


def json_type() -> JSON:
    return JSON().with_variant(JSONB(), "postgresql")


def _one_of(column: str, values: type[StrEnum]) -> str:
    allowed = ", ".join(f"'{member.value}'" for member in values)
    return f"{column} IN ({allowed})"


class Base(DeclarativeBase):
    pass


class AssertionRow(Base):
    __tablename__ = "assertions"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_assertion_confidence"),
        CheckConstraint("confirmation_count >= 1", name="ck_assertion_confirmations"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from",
            name="ck_assertion_valid_range",
        ),
        CheckConstraint(_one_of("kind", AssertionKind), name="ck_assertion_kind"),
        CheckConstraint(_one_of("origin", AssertionOrigin), name="ck_assertion_origin"),
        CheckConstraint(_one_of("status", AssertionStatus), name="ck_assertion_status"),
        CheckConstraint(_one_of("sensitivity", Sensitivity), name="ck_assertion_sensitivity"),
        CheckConstraint(
            _one_of("embedding_state", EmbeddingState), name="ck_assertion_embedding_state"
        ),
        Index("ix_assertions_topics", "topics", postgresql_using="gin"),
        Index("ix_assertions_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_assertions_current", "status", "created_at"),
        Index("ix_assertions_kind_origin", "kind", "origin"),
        Index("ix_assertions_embedding_state", "embedding_state"),
        Index("ix_assertion_review", "review_after", "id"),
        Index("ix_assertion_context_facet", "context_id", "facet"),
        CheckConstraint("revision >= 1", name="ck_maintenance_revision"),
        CheckConstraint(
            "evidence_state IN ('unknown','reported','inferred','verified')",
            name="ck_maintenance_evidence",
        ),
        CheckConstraint(
            "temporal_role IN ('unspecified','ongoing','historical')",
            name="ck_maintenance_temporal",
        ),
        CheckConstraint(
            "workflow_state IS NULL OR (kind = 'plan' AND workflow_state IN "
            "('proposed','active','completed','cancelled')) OR (kind = 'open_question' "
            "AND workflow_state IN ('unresolved','resolved'))",
            name="ck_maintenance_workflow",
        ),
        CheckConstraint(
            "evidence_state <> 'verified' OR last_verified_at IS NOT NULL",
            name="ck_maintenance_verified",
        ),
        CheckConstraint(
            "jsonb_typeof(provenance) = 'array' AND jsonb_array_length(provenance) <= 16",
            name="ck_maintenance_provenance",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content: Mapped[str] = mapped_column(Text)
    normalized_content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(40))
    origin: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="current")
    confidence: Mapped[float] = mapped_column(Float)
    topics: Mapped[list[str]] = mapped_column(ARRAY(String(80)), default=list)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    confirmation_count: Mapped[int] = mapped_column(Integer, default=1)
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
    evidence_state: Mapped[str] = mapped_column(String(20), server_default="unknown")
    temporal_role: Mapped[str] = mapped_column(String(20), server_default="unspecified")
    workflow_state: Mapped[str | None] = mapped_column(String(20))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    context_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_contexts.id", ondelete="RESTRICT", name="fk_assertion_context"),
    )
    facet: Mapped[str | None] = mapped_column(String(80))
    provenance: Mapped[list[dict[str, Any]]] = mapped_column(json_type(), server_default="[]")
    sensitivity: Mapped[str] = mapped_column(String(20), default="normal")
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assertions.id", ondelete="RESTRICT")
    )
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(384))
    replacement_embedding: Mapped[list[float] | None] = mapped_column(VECTOR(384))
    embedding_model: Mapped[str | None] = mapped_column(String(500))
    replacement_embedding_model: Mapped[str | None] = mapped_column(String(500))
    embedding_state: Mapped[str] = mapped_column(String(20), default="pending")
    search_vector: Mapped[Any | None] = mapped_column(TSVECTOR)

    sources: Mapped[list["SourceRow"]] = relationship(
        back_populates="assertion", cascade="all, delete-orphan", lazy="selectin"
    )
    # Read-only: links are written as `AssertionArtifactRow` rows by the ingestion service.
    artifacts: Mapped[list["ArtifactRow"]] = relationship(
        secondary="assertion_artifacts", viewonly=True, lazy="selectin"
    )


class TemporalEvidenceRow(Base):
    __tablename__ = "temporal_evidence"
    __table_args__ = (
        CheckConstraint("length(evidence_key) BETWEEN 1 AND 200", name="ck_temporal_key"),
        CheckConstraint("length(identity_hash) = 64", name="ck_temporal_identity"),
        CheckConstraint("length(payload_hash) = 64", name="ck_temporal_payload_hash"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_temporal_payload"),
        Index("ix_temporal_identity_recorded", "identity_hash", "recorded_at", "evidence_key"),
        Index("ix_temporal_assertion", "assertion_id"),
    )
    evidence_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    assertion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assertions.id", ondelete="CASCADE")
    )
    identity_hash: Mapped[str] = mapped_column(String(64))
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(json_type())
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TemporalRetirementRow(Base):
    """A retained claim stays invalid after forgetting its correcting assertion's content."""

    __tablename__ = "temporal_retirements"
    evidence_key: Mapped[str] = mapped_column(
        String(200),
        ForeignKey("temporal_evidence.evidence_key", ondelete="CASCADE"),
        primary_key=True,
    )
    retired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceRow(Base):
    __tablename__ = "assertion_sources"
    __table_args__ = (UniqueConstraint("assertion_id", "url", name="uq_source_assertion_url"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assertion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assertions.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(String(500))
    publisher: Mapped[str | None] = mapped_column(String(200))
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assertion: Mapped[AssertionRow] = relationship(back_populates="sources")


class FlushBatchRow(Base):
    __tablename__ = "flush_batches"
    __table_args__ = (
        UniqueConstraint("principal_id", "idempotency_hash", name="uq_batch_idempotency"),
        CheckConstraint("declared_parts > 0", name="ck_batch_declared_parts"),
        CheckConstraint("declared_items > 0", name="ck_batch_declared_items"),
        CheckConstraint(
            "request_digest IS NULL OR length(request_digest) = 64", name="ck_batch_request_digest"
        ),
        CheckConstraint(_one_of("state", BatchState), name="ck_batch_state"),
        Index("ix_flush_batches_expiry", "state", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    principal_id: Mapped[str] = mapped_column(String(128))
    idempotency_hash: Mapped[str] = mapped_column(String(64))
    request_digest: Mapped[str | None] = mapped_column(String(64))
    declared_parts: Mapped[int] = mapped_column(Integer)
    declared_items: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(json_type())

    parts: Mapped[list["FlushPartRow"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", lazy="selectin"
    )

    # Transient, unmapped: set by the service when `begin` returned an existing batch.
    replayed = False


class FlushPartRow(Base):
    __tablename__ = "flush_parts"
    __table_args__ = (UniqueConstraint("batch_id", "part_number", name="uq_batch_part"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("flush_batches.id", ondelete="CASCADE"), index=True
    )
    part_number: Mapped[int] = mapped_column(Integer)
    payload_hash: Mapped[str] = mapped_column(String(64))
    item_count: Mapped[int] = mapped_column(Integer)
    payload: Mapped[list[dict[str, Any]]] = mapped_column(json_type())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    batch: Mapped[FlushBatchRow] = relationship(back_populates="parts")


class EmbeddingJobRow(Base):
    __tablename__ = "embedding_jobs"
    __table_args__ = (
        UniqueConstraint("assertion_id", "model", name="uq_embedding_job_assertion_model"),
        CheckConstraint("attempts >= 0", name="ck_embedding_job_attempts"),
        CheckConstraint(_one_of("state", JobState), name="ck_embedding_job_state"),
        Index("ix_embedding_jobs_claim", "state", "available_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assertion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assertions.id", ondelete="CASCADE"), index=True
    )
    model: Mapped[str] = mapped_column(String(500))
    replace_existing: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(String(128))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ConflictRow(Base):
    __tablename__ = "knowledge_conflicts"
    __table_args__ = (
        UniqueConstraint("left_assertion_id", "right_assertion_id", name="uq_conflict_pair"),
        CheckConstraint(
            "left_assertion_id <> right_assertion_id", name="ck_conflict_distinct_assertions"
        ),
        Index("ix_conflicts_unresolved", "resolved_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    left_assertion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assertions.id", ondelete="CASCADE")
    )
    right_assertion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assertions.id", ondelete="CASCADE")
    )
    reason: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeletionAuditRow(Base):
    __tablename__ = "deletion_audit"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    principal_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(40), default="hard_delete")
    deleted_count: Mapped[int] = mapped_column(Integer)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConfirmationTokenRow(Base):
    __tablename__ = "confirmation_tokens"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    principal_id: Mapped[str] = mapped_column(String(128))
    assertion_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HeartbeatRow(Base):
    """When a background duty last succeeded. Content-free operational data."""

    __tablename__ = "operational_heartbeats"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    succeeded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    detail: Mapped[str | None] = mapped_column(String(200))


class ArtifactRow(Base):
    """A file-like piece of knowledge, stored whole and linked to the assertions describing it."""

    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("principal_id", "idempotency_hash", name="uq_artifact_idempotency"),
        CheckConstraint("declared_chunks > 0", name="ck_artifact_declared_chunks"),
        CheckConstraint(_one_of("state", ArtifactState), name="ck_artifact_state"),
        CheckConstraint(_one_of("storage", ArtifactStorage), name="ck_artifact_storage"),
        CheckConstraint(
            "state <> 'stored' OR (content_text IS NOT NULL AND content_sha256 IS NOT NULL "
            "AND size_bytes IS NOT NULL)",
            name="ck_artifact_stored_content",
        ),
        CheckConstraint(
            "(state = 'duplicate') = (duplicate_of IS NOT NULL)", name="ck_artifact_duplicate_of"
        ),
        # One stored record per content; a second upload of the same text becomes a duplicate.
        Index(
            "uq_artifacts_stored_content",
            "content_sha256",
            unique=True,
            postgresql_where="state = 'stored'",
        ),
        Index("ix_artifacts_state_expiry", "state", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    principal_id: Mapped[str] = mapped_column(String(128))
    idempotency_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="open")
    storage: Mapped[str] = mapped_column(String(10), default="text")
    filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(1000))
    declared_chunks: Mapped[int] = mapped_column(Integer)
    # Deferred: listing an assertion's artifacts must not load their content.
    content_text: Mapped[str | None] = deferred(mapped_column(Text))
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Transient, unmapped: set by the service when a call repeated an earlier one.
    replayed = False


class ArtifactChunkRow(Base):
    """Staged upload text. Deleted when the artifact is committed, rejected, or expires."""

    __tablename__ = "artifact_chunks"

    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id", ondelete="CASCADE"), primary_key=True
    )
    chunk_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssertionArtifactRow(Base):
    __tablename__ = "assertion_artifacts"
    __table_args__ = (Index("ix_assertion_artifacts_artifact", "artifact_id"),)

    assertion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assertions.id", ondelete="CASCADE"), primary_key=True
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeContextRow(Base):
    __tablename__ = "knowledge_contexts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String(200)))


class AssertionLinkRow(Base):
    __tablename__ = "assertion_links"
    __table_args__ = (
        CheckConstraint("source_id <> target_id", name="ck_link_endpoints"),
        CheckConstraint(
            "kind IN ('part_of','depends_on','supports','equivalent_to','resolves')",
            name="ck_link_kind",
        ),
        CheckConstraint(
            "position IS NULL OR (kind = 'part_of' AND position >= 0 AND position <= 9999)",
            name="ck_link_position",
        ),
        Index("uq_link_position", "target_id", "position", unique=True),
        Index("ix_link_target", "target_id"),
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assertions.id", ondelete="CASCADE"), primary_key=True
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assertions.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    position: Mapped[int | None] = mapped_column(Integer)


class TopicAliasRow(Base):
    __tablename__ = "topic_aliases"
    alias: Mapped[str] = mapped_column(String(80), primary_key=True)
    canonical: Mapped[str] = mapped_column(String(80))


class AssertionUsageRow(Base):
    __tablename__ = "assertion_usage"
    __table_args__ = (
        CheckConstraint(
            "retrieval_count >= 0 AND explicit_fetch_count >= 0 AND use_count >= 0 "
            "AND explicit_fetch_count <= retrieval_count",
            name="ck_usage_counts",
        ),
    )
    assertion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assertions.id", ondelete="CASCADE"), primary_key=True
    )
    retrieval_count: Mapped[int] = mapped_column(BigInteger, server_default="0")
    explicit_fetch_count: Mapped[int] = mapped_column(BigInteger, server_default="0")
    use_count: Mapped[int] = mapped_column(BigInteger, server_default="0")
    last_retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_explicit_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MaintenanceEventRow(Base):
    """Bounded retry receipts and metadata audit; never old/new assertion values."""

    __tablename__ = "maintenance_events"
    __table_args__ = (Index("ix_maintenance_expiry", "expires_at"),)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    assertion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assertions.id", ondelete="CASCADE"), primary_key=True
    )
    operation: Mapped[str] = mapped_column(String(30))
    payload_hash: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int | None] = mapped_column(Integer)
    fields: Mapped[list[str]] = mapped_column(ARRAY(String(80)))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
