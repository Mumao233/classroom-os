"""PR-07a immutable classroom sources, plans and question projections."""
from pathlib import Path

from alembic import context, op

revision = 'pr07a_001'
down_revision = '0009_private_media'
branch_labels = None
depends_on = None


def upgrade():
    directory = Path(__file__).parent
    for filename in ('pr07a_001_qa.sql', 'pr07a_task_functions.sql'):
        sql = (directory / filename).read_text(encoding='utf8')
        if context.is_offline_mode():
            op.execute(sql.replace(':', r'\:'))
        else:
            op.get_bind().execution_options(no_parameters=True).exec_driver_sql(sql)


def downgrade():
    raise RuntimeError('preserve source snapshots, questions, send evidence and billing records; use forward repair')
