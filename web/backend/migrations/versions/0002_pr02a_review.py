"""PR-02a independent-review fixes; preserve the applied initial revision."""
from pathlib import Path

from alembic import context, op

revision = "0002_pr02a_review"
down_revision = "0001_data_foundation"
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    if context.is_offline_mode():
        op.execute(sql.replace(":", r"\:"))
    else:
        op.get_bind().exec_driver_sql(sql)


def downgrade():
    raise RuntimeError(
        "PR-02a review downgrade refused: retain material revocation consistency "
        "and contract limits; roll back compatible code while retaining schema"
    )
