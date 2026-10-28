"""Initial PR-02a schema, with a frozen SQL snapshot independent of application models."""

from pathlib import Path

from alembic import context, op

revision = "0001_data_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # The adjacent immutable SQL file is part of this migration revision.
    snapshot = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    if context.is_offline_mode():
        # Alembic/SQLAlchemy text would otherwise interpret JSON colons as binds.
        op.execute(snapshot.replace(":", r"\:"))
    else:
        op.get_bind().exec_driver_sql(snapshot)


def downgrade():
    raise RuntimeError(
        "PR-02a downgrade refused: preserve learning data and deletion ledger; "
        "roll back compatible application code without dropping schema"
    )
