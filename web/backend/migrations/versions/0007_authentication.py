"""PR-03b authenticated sessions and factor lifecycle, additive to 0006."""
from pathlib import Path
from alembic import context,op

revision='0007_authentication'
down_revision='0006_local_reconciler_security'
branch_labels=None
depends_on=None


def upgrade():
    sql=Path(__file__).with_suffix('.sql').read_text(encoding='utf8')
    if context.is_offline_mode():op.execute(sql.replace(':',r'\:'))
    else:op.get_bind().execution_options(no_parameters=True).exec_driver_sql(sql)


def downgrade():
    raise RuntimeError('authentication downgrade refused: revoke access and retain security history; use compatible code rollback')
