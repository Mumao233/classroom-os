"""Default-off shared PR-05a dependency consumed by PR-05b and PR-08a."""
from pathlib import Path
from alembic import context, op

revision = '0011_cache_keys'
down_revision = '0010_deletion_lifecycle'
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).with_suffix('.sql').read_text(encoding='utf8')
    if context.is_offline_mode():
        op.execute(sql.replace(':', r'\:'))
    else:
        op.get_bind().execution_options(no_parameters=True).exec_driver_sql(sql)


def downgrade():
    raise RuntimeError('preserve wrapped cache keys and revocations; use forward repair')
