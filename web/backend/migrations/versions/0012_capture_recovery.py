"""PR-05b owner-fenced capture, immutable chunks and bounded recovery."""
from pathlib import Path
from alembic import context, op

revision = '0012_capture_recovery'
down_revision = '0011_cache_keys'
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).with_suffix('.sql').read_text(encoding='utf8')
    if context.is_offline_mode():
        op.execute(sql.replace(':', r'\:'))
    else:
        op.get_bind().execution_options(no_parameters=True).exec_driver_sql(sql)


def downgrade():
    raise RuntimeError('preserve capture fences, manifests and receipts; use forward repair')
