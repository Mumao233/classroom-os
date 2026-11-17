"""Restore the default-closed trusted-device database invariant."""
from pathlib import Path
from alembic import context, op

revision = '0013_trusted_device_guard'
down_revision = '0012_capture_recovery'
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).with_suffix('.sql').read_text(encoding='utf8')
    if context.is_offline_mode():
        op.execute(sql)
    else:
        op.get_bind().execution_options(no_parameters=True).exec_driver_sql(sql)


def downgrade():
    raise RuntimeError('trusted-device enablement requires a reviewed forward migration')
