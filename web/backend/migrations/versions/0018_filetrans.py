"""Isolated synthetic Filetrans plans, attempts and transcript candidates."""
from pathlib import Path

from alembic import context, op

revision = '0018_filetrans'
down_revision = '0017_realtime_asr'
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).with_suffix('.sql').read_text(encoding='utf8')
    if context.is_offline_mode():
        op.execute(sql.replace(':', r'\:'))
    else:
        op.get_bind().execution_options(no_parameters=True).exec_driver_sql(sql)


def downgrade():
    raise RuntimeError('Filetrans accounting and adopted versions require a forward repair')
