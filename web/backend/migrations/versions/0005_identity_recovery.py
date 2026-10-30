"""Align metadata-only expired-task recovery with PR-03a identity eligibility."""
from pathlib import Path

from alembic import context, op

revision = "0005_identity_recovery"
down_revision = "0004_identity"
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    if context.is_offline_mode():
        op.execute(sql.replace(":", r"\:"))
    else:
        op.get_bind().execution_options(no_parameters=True).exec_driver_sql(sql)


def downgrade():
    raise RuntimeError(
        "PR-03a recovery downgrade refused: retain identity eligibility and "
        "task risk evidence; roll back compatible code or repair forward"
    )
