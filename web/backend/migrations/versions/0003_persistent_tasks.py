"""PR-02b persistent task engine; additive to accepted PR-02a history."""
from pathlib import Path

from alembic import context, op

revision = "0003_persistent_tasks"
down_revision = "0002_pr02a_review"
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
        "PR-02b downgrade refused: stop claims and roll back compatible code; "
        "retain task/attempt/result/receipt/event ledgers, especially unknown charges"
    )
