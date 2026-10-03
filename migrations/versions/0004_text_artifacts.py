"""Store text artifacts and link them to the assertions that describe them."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_text_artifacts"
down_revision = "0003_operational_heartbeats"
branch_labels = None
depends_on = None


def _timestamp(name: str, *, default: bool = True, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.func.now() if default else None,
        nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("principal_id", sa.String(128), nullable=False),
        sa.Column("idempotency_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("storage", sa.String(10), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("description", sa.String(1000)),
        sa.Column("declared_chunks", sa.Integer, nullable=False),
        sa.Column("content_text", sa.Text),
        sa.Column("content_sha256", sa.String(64)),
        sa.Column("size_bytes", sa.Integer),
        sa.Column(
            "duplicate_of",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="CASCADE"),
        ),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _timestamp("expires_at", default=False),
        _timestamp("stored_at", default=False, nullable=True),
        sa.UniqueConstraint("principal_id", "idempotency_hash", name="uq_artifact_idempotency"),
        sa.CheckConstraint("declared_chunks > 0", name="ck_artifact_declared_chunks"),
        sa.CheckConstraint("state IN ('open', 'stored', 'duplicate')", name="ck_artifact_state"),
        sa.CheckConstraint("storage IN ('text')", name="ck_artifact_storage"),
        sa.CheckConstraint(
            "state <> 'stored' OR (content_text IS NOT NULL AND content_sha256 IS NOT NULL "
            "AND size_bytes IS NOT NULL)",
            name="ck_artifact_stored_content",
        ),
        sa.CheckConstraint(
            "(state = 'duplicate') = (duplicate_of IS NOT NULL)", name="ck_artifact_duplicate_of"
        ),
    )
    op.create_index(
        "uq_artifacts_stored_content",
        "artifacts",
        ["content_sha256"],
        unique=True,
        postgresql_where=sa.text("state = 'stored'"),
    )
    op.create_index("ix_artifacts_state_expiry", "artifacts", ["state", "expires_at"])

    op.create_table(
        "artifact_chunks",
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("chunk_number", sa.Integer, primary_key=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        _timestamp("created_at"),
    )

    op.create_table(
        "assertion_artifacts",
        sa.Column(
            "assertion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assertions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        _timestamp("created_at"),
    )
    op.create_index("ix_assertion_artifacts_artifact", "assertion_artifacts", ["artifact_id"])


def downgrade() -> None:
    op.drop_table("assertion_artifacts")
    op.drop_table("artifact_chunks")
    op.drop_table("artifacts")
