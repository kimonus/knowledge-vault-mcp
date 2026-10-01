"""Enforce enumerated column values with database CHECK constraints."""

from alembic import op

revision = "0002_enumerated_value_checks"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

# Values are frozen here on purpose: a later change to an enumeration needs its own revision.
_CHECKS = (
    (
        "assertions",
        "ck_assertion_kind",
        "kind IN ('user_fact', 'preference', 'external_fact', 'derived_conclusion', 'decision', "
        "'procedure', 'configuration', 'considered_option', 'rejected_option', 'plan', "
        "'open_question', 'artifact_observation')",
    ),
    (
        "assertions",
        "ck_assertion_origin",
        "origin IN ('user', 'assistant', 'joint', 'external_source', 'artifact')",
    ),
    (
        "assertions",
        "ck_assertion_status",
        "status IN ('current', 'uncertain', 'disputed', 'superseded')",
    ),
    ("assertions", "ck_assertion_sensitivity", "sensitivity IN ('normal', 'private', 'sensitive')"),
    (
        "assertions",
        "ck_assertion_embedding_state",
        "embedding_state IN ('pending', 'ready', 'failed', 'disabled')",
    ),
    ("flush_batches", "ck_batch_state", "state IN ('open', 'committed', 'aborted', 'expired')"),
    (
        "embedding_jobs",
        "ck_embedding_job_state",
        "state IN ('pending', 'claimed', 'retry', 'completed', 'dead')",
    ),
)


def upgrade() -> None:
    for table, name, condition in _CHECKS:
        op.create_check_constraint(name, table, condition)


def downgrade() -> None:
    for table, name, _ in reversed(_CHECKS):
        op.drop_constraint(name, table, type_="check")
