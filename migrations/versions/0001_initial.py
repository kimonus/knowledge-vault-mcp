"""Initial assertion, staging, retrieval, auth support, and embedding queue schema.

The schema is written out explicitly. A revision must keep creating the same objects no matter
how the ORM models evolve later, so it never derives DDL from `Base.metadata`.
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

_UUID = postgresql.UUID(as_uuid=True)
_TZ = sa.DateTime(timezone=True)


def _now(name: str) -> sa.Column:
    return sa.Column(name, _TZ, server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "assertions",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("normalized_content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("origin", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("topics", postgresql.ARRAY(sa.String(80)), nullable=False),
        sa.Column("valid_from", _TZ),
        sa.Column("valid_to", _TZ),
        sa.Column("observed_at", _TZ),
        _now("created_at"),
        _now("updated_at"),
        _now("last_confirmed_at"),
        sa.Column("confirmation_count", sa.Integer(), nullable=False),
        sa.Column("sensitivity", sa.String(20), nullable=False),
        sa.Column("supersedes_id", _UUID, sa.ForeignKey("assertions.id", ondelete="RESTRICT")),
        sa.Column("embedding", VECTOR(384)),
        sa.Column("replacement_embedding", VECTOR(384)),
        sa.Column("embedding_model", sa.String(500)),
        sa.Column("replacement_embedding_model", sa.String(500)),
        sa.Column("embedding_state", sa.String(20), nullable=False),
        sa.Column("search_vector", postgresql.TSVECTOR()),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_assertion_confidence"),
        sa.CheckConstraint("confirmation_count >= 1", name="ck_assertion_confirmations"),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from",
            name="ck_assertion_valid_range",
        ),
    )
    op.create_index("ix_assertions_topics", "assertions", ["topics"], postgresql_using="gin")
    op.create_index(
        "ix_assertions_search_vector", "assertions", ["search_vector"], postgresql_using="gin"
    )
    op.create_index("ix_assertions_current", "assertions", ["status", "created_at"])
    op.create_index("ix_assertions_kind_origin", "assertions", ["kind", "origin"])
    op.create_index("ix_assertions_embedding_state", "assertions", ["embedding_state"])

    op.create_table(
        "assertion_sources",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "assertion_id",
            _UUID,
            sa.ForeignKey("assertions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(500)),
        sa.Column("publisher", sa.String(200)),
        sa.Column("retrieved_at", _TZ),
        sa.UniqueConstraint("assertion_id", "url", name="uq_source_assertion_url"),
    )
    op.create_index("ix_assertion_sources_assertion_id", "assertion_sources", ["assertion_id"])

    op.create_table(
        "flush_batches",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("principal_id", sa.String(128), nullable=False),
        sa.Column("idempotency_hash", sa.String(64), nullable=False),
        sa.Column("declared_parts", sa.Integer(), nullable=False),
        sa.Column("declared_items", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        _now("created_at"),
        _now("updated_at"),
        sa.Column("expires_at", _TZ, nullable=False),
        sa.Column("committed_at", _TZ),
        sa.Column("result", postgresql.JSONB()),
        sa.UniqueConstraint("principal_id", "idempotency_hash", name="uq_batch_idempotency"),
        sa.CheckConstraint("declared_parts > 0", name="ck_batch_declared_parts"),
        sa.CheckConstraint("declared_items > 0", name="ck_batch_declared_items"),
    )
    op.create_index("ix_flush_batches_expiry", "flush_batches", ["state", "expires_at"])

    op.create_table(
        "flush_parts",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "batch_id",
            _UUID,
            sa.ForeignKey("flush_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("part_number", sa.Integer(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        _now("created_at"),
        sa.UniqueConstraint("batch_id", "part_number", name="uq_batch_part"),
    )
    op.create_index("ix_flush_parts_batch_id", "flush_parts", ["batch_id"])

    op.create_table(
        "embedding_jobs",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "assertion_id",
            _UUID,
            sa.ForeignKey("assertions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model", sa.String(500), nullable=False),
        sa.Column("replace_existing", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        _now("available_at"),
        sa.Column("claimed_at", _TZ),
        sa.Column("claimed_by", sa.String(128)),
        sa.Column("last_error_code", sa.String(80)),
        _now("created_at"),
        _now("updated_at"),
        sa.UniqueConstraint("assertion_id", "model", name="uq_embedding_job_assertion_model"),
        sa.CheckConstraint("attempts >= 0", name="ck_embedding_job_attempts"),
    )
    op.create_index("ix_embedding_jobs_assertion_id", "embedding_jobs", ["assertion_id"])
    op.create_index("ix_embedding_jobs_claim", "embedding_jobs", ["state", "available_at"])

    op.create_table(
        "knowledge_conflicts",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "left_assertion_id",
            _UUID,
            sa.ForeignKey("assertions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "right_assertion_id",
            _UUID,
            sa.ForeignKey("assertions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(80), nullable=False),
        _now("created_at"),
        sa.Column("resolved_at", _TZ),
        sa.UniqueConstraint("left_assertion_id", "right_assertion_id", name="uq_conflict_pair"),
        sa.CheckConstraint(
            "left_assertion_id <> right_assertion_id", name="ck_conflict_distinct_assertions"
        ),
    )
    op.create_index("ix_conflicts_unresolved", "knowledge_conflicts", ["resolved_at"])

    op.create_table(
        "deletion_audit",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("principal_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("deleted_count", sa.Integer(), nullable=False),
        sa.Column("correlation_id", _UUID, nullable=False),
        _now("created_at"),
    )

    op.create_table(
        "confirmation_tokens",
        sa.Column("token_hash", sa.LargeBinary(32), primary_key=True),
        sa.Column("principal_id", sa.String(128), nullable=False),
        sa.Column("assertion_ids", postgresql.ARRAY(_UUID), nullable=False),
        sa.Column("expires_at", _TZ, nullable=False),
        sa.Column("used_at", _TZ),
        _now("created_at"),
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION knowledge_vault_search_vector_update() RETURNS trigger AS $$
        BEGIN
          NEW.search_vector := to_tsvector(
            'simple',
            coalesce(NEW.content, '') || ' ' || coalesce(array_to_string(NEW.topics, ' '), '')
          );
          RETURN NEW;
        END
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_assertions_search_vector
        BEFORE INSERT OR UPDATE OF content, topics ON assertions
        FOR EACH ROW EXECUTE FUNCTION knowledge_vault_search_vector_update()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_assertions_search_vector ON assertions")
    op.execute("DROP FUNCTION IF EXISTS knowledge_vault_search_vector_update()")
    for table in (
        "confirmation_tokens",
        "deletion_audit",
        "knowledge_conflicts",
        "embedding_jobs",
        "flush_parts",
        "flush_batches",
        "assertion_sources",
        "assertions",
    ):
        op.drop_table(table)
