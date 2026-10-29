"""Add maintenance metadata without changing legacy claim meaning or wire fields."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0005_knowledge_maintenance"
down_revision = "0004_text_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_contexts",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("aliases", pg.ARRAY(sa.String(200)), nullable=False),
    )
    for column in (
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        sa.Column("evidence_state", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("temporal_role", sa.String(20), nullable=False, server_default="unspecified"),
        sa.Column("workflow_state", sa.String(20)),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.Column("review_after", sa.DateTime(timezone=True)),
        sa.Column("context_id", pg.UUID(as_uuid=True)),
        sa.Column("facet", sa.String(80)),
        sa.Column("provenance", pg.JSONB, nullable=False, server_default="[]"),
    ):
        op.add_column("assertions", column)
    op.create_foreign_key(
        "fk_assertion_context",
        "assertions",
        "knowledge_contexts",
        ["context_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    # New checks are compatible with old writers relying on the column defaults.
    for name, expression in (
        ("revision", "revision >= 1"),
        ("evidence", "evidence_state IN ('unknown','reported','inferred','verified')"),
        ("temporal", "temporal_role IN ('unspecified','ongoing','historical')"),
        (
            "workflow",
            "workflow_state IS NULL OR (kind = 'plan' AND workflow_state IN "
            "('proposed','active','completed','cancelled')) OR (kind = 'open_question' "
            "AND workflow_state IN ('unresolved','resolved'))",
        ),
        ("verified", "evidence_state <> 'verified' OR last_verified_at IS NOT NULL"),
        (
            "provenance",
            "jsonb_typeof(provenance) = 'array' AND jsonb_array_length(provenance) <= 16",
        ),
    ):
        op.create_check_constraint("ck_maintenance_" + name, "assertions", expression)
    op.create_index("ix_assertion_review", "assertions", ["review_after", "id"])
    op.create_index("ix_assertion_context_facet", "assertions", ["context_id", "facet"])
    op.create_table(
        "assertion_links",
        sa.Column(
            "source_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("assertions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "target_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("assertions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("kind", sa.String(20), primary_key=True),
        sa.Column("position", sa.Integer),
        sa.CheckConstraint("source_id <> target_id", name="ck_link_endpoints"),
        sa.CheckConstraint(
            "kind IN ('part_of','depends_on','supports','equivalent_to','resolves')",
            name="ck_link_kind",
        ),
        sa.CheckConstraint(
            "position IS NULL OR (kind = 'part_of' AND position >= 0 AND position <= 9999)",
            name="ck_link_position",
        ),
    )
    op.create_index("uq_link_position", "assertion_links", ["target_id", "position"], unique=True)
    op.create_index("ix_link_target", "assertion_links", ["target_id"])
    op.create_table(
        "topic_aliases",
        sa.Column("alias", sa.String(80), primary_key=True),
        sa.Column("canonical", sa.String(80), nullable=False),
    )
    op.create_table(
        "assertion_usage",
        sa.Column(
            "assertion_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("assertions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("retrieval_count", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("explicit_fetch_count", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("use_count", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("last_retrieved_at", sa.DateTime(timezone=True)),
        sa.Column("last_explicit_fetch_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "retrieval_count >= 0 AND explicit_fetch_count >= 0 AND use_count >= 0 AND explicit_fetch_count <= retrieval_count",
            name="ck_usage_counts",
        ),
    )
    op.create_table(
        "maintenance_events",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column(
            "assertion_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("assertions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("operation", sa.String(30), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer),
        sa.Column("fields", pg.ARRAY(sa.String(80)), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_maintenance_expiry", "maintenance_events", ["expires_at"])
    op.execute("""
        CREATE FUNCTION bump_assertion_revision() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (to_jsonb(NEW) - ARRAY['embedding','embedding_model','embedding_state',
              'replacement_embedding','replacement_embedding_model','search_vector',
              'updated_at','revision','last_confirmed_at','confirmation_count'])
             IS DISTINCT FROM
             (to_jsonb(OLD) - ARRAY['embedding','embedding_model','embedding_state',
              'replacement_embedding','replacement_embedding_model','search_vector',
              'updated_at','revision','last_confirmed_at','confirmation_count']) THEN
            NEW.revision := OLD.revision + 1;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER maintenance_revision BEFORE UPDATE ON assertions
          FOR EACH ROW EXECUTE FUNCTION bump_assertion_revision();
    """)


def downgrade() -> None:
    # Destructive for metadata introduced by this release. Prefer application rollback.
    op.execute("DROP TRIGGER maintenance_revision ON assertions")
    op.execute("DROP FUNCTION bump_assertion_revision()")
    for table in ("maintenance_events", "assertion_usage", "topic_aliases", "assertion_links"):
        op.drop_table(table)
    op.drop_index("ix_assertion_context_facet", "assertions")
    op.drop_index("ix_assertion_review", "assertions")
    for name in ("revision", "evidence", "temporal", "workflow", "verified", "provenance"):
        op.drop_constraint("ck_maintenance_" + name, "assertions", type_="check")
    op.drop_constraint("fk_assertion_context", "assertions", type_="foreignkey")
    for column in (
        "provenance",
        "facet",
        "context_id",
        "review_after",
        "last_verified_at",
        "workflow_state",
        "temporal_role",
        "evidence_state",
        "revision",
    ):
        op.drop_column("assertions", column)
    op.drop_table("knowledge_contexts")
