"""PR-03b manual recovery state and segregated control functions."""
from pathlib import Path
from alembic import context,op
revision='0008_manual_recovery'
down_revision='0007_authentication'
branch_labels=None
depends_on=None


def upgrade():
    sql=Path(__file__).with_suffix('.sql').read_text(encoding='utf8')
    if context.is_offline_mode():op.execute(sql.replace(':',r'\:'))
    else:op.get_bind().execution_options(no_parameters=True).exec_driver_sql(sql)


def downgrade():raise RuntimeError('manual recovery downgrade refused: preserve authority high-water marks, revocations and audit facts')
