"""Preserve immutable source evidence without assigning chronology to legacy assertions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0006_temporal_evidence"
down_revision = "0005_knowledge_maintenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("flush_batches", sa.Column("request_digest", sa.String(64)))
    op.create_check_constraint(
        "ck_batch_request_digest",
        "flush_batches",
        "request_digest IS NULL OR length(request_digest) = 64",
    )
    op.create_table(
        "temporal_evidence",
        sa.Column("evidence_key", sa.String(200), primary_key=True),
        sa.Column(
            "assertion_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("assertions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("identity_hash", sa.String(64), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("length(evidence_key) BETWEEN 1 AND 200", name="ck_temporal_key"),
        sa.CheckConstraint("length(identity_hash) = 64", name="ck_temporal_identity"),
        sa.CheckConstraint("length(payload_hash) = 64", name="ck_temporal_payload_hash"),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_temporal_payload"),
    )
    op.create_index(
        "ix_temporal_identity_recorded",
        "temporal_evidence",
        ["identity_hash", "recorded_at", "evidence_key"],
    )
    op.create_index("ix_temporal_assertion", "temporal_evidence", ["assertion_id"])
    op.create_table(
        "temporal_retirements",
        sa.Column(
            "evidence_key",
            sa.String(200),
            sa.ForeignKey("temporal_evidence.evidence_key", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "retired_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    # Evidence can only be replaced by an explicit new record, or erased by hard deletion.
    op.execute("""
        CREATE FUNCTION refuse_temporal_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'temporal evidence is immutable' USING ERRCODE = '23514';
        END $$;
        CREATE TRIGGER temporal_immutable BEFORE UPDATE ON temporal_evidence
          FOR EACH ROW EXECUTE FUNCTION refuse_temporal_update();
    """)


def downgrade() -> None:
    # Destructive for new evidence only. Prefer rolling back application images.
    op.drop_table("temporal_retirements")
    op.drop_table("temporal_evidence")
    op.execute("DROP FUNCTION refuse_temporal_update()")
    op.drop_constraint("ck_batch_request_digest", "flush_batches", type_="check")
    op.drop_column("flush_batches", "request_digest")
