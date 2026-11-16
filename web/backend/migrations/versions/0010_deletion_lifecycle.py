"""PR-04b deletion inventory, execution state, narrow eraser functions and retention authority."""
from pathlib import Path
from alembic import context, op

revision = '0010_deletion_lifecycle'
down_revision = 'pr07b_001'
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).with_suffix('.sql').read_text(encoding='utf8')
    if context.is_offline_mode():
        op.execute(sql.replace(':', r'\:'))
    else:
        op.get_bind().execution_options(no_parameters=True).exec_driver_sql(sql)


def downgrade():
    raise RuntimeError('preserve tombstones, deletion inventories and revocations; use forward repair')
