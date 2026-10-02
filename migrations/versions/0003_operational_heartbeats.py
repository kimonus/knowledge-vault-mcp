"""Record when background duties (worker maintenance, backups) last succeeded."""

import sqlalchemy as sa
from alembic import op

revision = "0003_operational_heartbeats"
down_revision = "0002_enumerated_value_checks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operational_heartbeats",
        sa.Column("name", sa.String(40), primary_key=True),
        sa.Column(
            "succeeded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("detail", sa.String(200)),
    )


def downgrade() -> None:
    op.drop_table("operational_heartbeats")
