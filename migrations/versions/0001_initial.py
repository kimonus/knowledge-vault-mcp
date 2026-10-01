"""Initial assertion, staging, retrieval, auth support, and embedding queue schema."""

from alembic import op

from knowledge_vault.persistence.tables import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)
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
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, checkfirst=True)
