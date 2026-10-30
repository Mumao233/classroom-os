"""PR-03a pending registration, invitations and internal identity controls."""
from pathlib import Path

from alembic import context, op

revision = "0004_identity"
down_revision = "0003_persistent_tasks"
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
        "PR-03a downgrade refused: pause registrations and roll back compatible "
        "code; retain pending, invitation, capacity and activation audit records"
    )
