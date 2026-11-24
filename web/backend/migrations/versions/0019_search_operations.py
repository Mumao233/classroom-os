"""Owner-scoped search, notifications, and private feedback."""
from pathlib import Path

from alembic import context, op

revision = '0019_search_operations'
down_revision = '0018_filetrans'
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).with_suffix('.sql').read_text(encoding='utf8')
    if context.is_offline_mode():
        op.execute(sql.replace(':', r'\:'))
    else:
        op.get_bind().execution_options(no_parameters=True).exec_driver_sql(sql)


def downgrade():
    raise RuntimeError('preserve notification and feedback evidence; use forward repair')
