"""PR-07b immutable review candidates, personal versions and conflict retention."""
from pathlib import Path

from alembic import context, op

revision = 'pr07b_001'
down_revision = 'pr07a_001'
branch_labels = None
depends_on = None


def upgrade():
    for name in ('pr07b_001_review.sql', 'pr07b_task_functions.sql'):
        sql = (Path(__file__).parent / name).read_text(encoding='utf8')
        if context.is_offline_mode():
            op.execute(sql.replace(':', r'\:'))
        else:
            op.get_bind().execution_options(no_parameters=True).exec_driver_sql(sql)


def downgrade():
    raise RuntimeError('preserve review history, personal drafts and billing evidence; use forward repair')
