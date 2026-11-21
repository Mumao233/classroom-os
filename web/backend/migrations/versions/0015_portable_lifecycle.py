"""Install owner-scoped portable transfer admission and lifecycle records."""
from pathlib import Path

from alembic import context, op

revision = '0015_portable_lifecycle'
down_revision = '0014_offline_sync'
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).with_suffix('.sql').read_text(encoding='utf8')
    if context.is_offline_mode():
        op.execute(sql.replace(':', r'\:'))
    else:
        op.get_bind().execution_options(no_parameters=True).exec_driver_sql(sql)


def downgrade():
    raise RuntimeError('portable lifecycle evidence requires a forward repair')
