"""PR-04a private media manifests, uploads and session-bound tickets."""
from pathlib import Path
from alembic import context, op

revision = '0009_private_media'
down_revision = '0008_manual_recovery'
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).with_suffix('.sql').read_text(encoding='utf8')
    if context.is_offline_mode():
        op.execute(sql.replace(':', r'\:'))
    else:
        op.get_bind().execution_options(no_parameters=True).exec_driver_sql(sql)


def downgrade():
    raise RuntimeError('preserve media manifests, object identities and revocations; use forward repair')
