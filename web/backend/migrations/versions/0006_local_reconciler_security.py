"""Apply the reviewed local reconciliation privilege and safety boundary."""
from pathlib import Path

from alembic import context, op

revision = "0006_local_reconciler_security"
down_revision = "0005_identity_recovery"
branch_labels = None
depends_on = None


def upgrade():
    # Replace bodies while the migration principal still manages the old
    # functions. The privilege script then transfers ownership and removes all
    # temporary rights. Both files belong to this single Alembic transaction.
    directory = Path(__file__).parent
    for name in ("0006_local_reconciler_functions.sql", "0006_local_reconciler_security.sql"):
        sql = (directory / name).read_text(encoding="utf-8")
        if context.is_offline_mode():
            op.execute(sql.replace(":", r"\:"))
        else:
            op.get_bind().execution_options(no_parameters=True).exec_driver_sql(sql)


def downgrade():
    raise RuntimeError(
        "PR-03a local reconciliation downgrade refused: retain raw, receipt, "
        "outbox and unknown-charge evidence; use compatible code or repair forward"
    )
