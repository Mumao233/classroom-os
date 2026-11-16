"""PR-07a metadata mirrors the frozen literal migration, without loading SQL."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from .schema import metadata

QA_COLUMNS = {
    'qa_sources': ('id', 'owner_id', 'created_at', 'class_session_id', 'kind',
                   'object_id', 'current_revision_id', 'origin', 'start_ms', 'end_ms', 'revoked_at'),
    'qa_source_versions': ('id', 'owner_id', 'created_at', 'source_id', 'class_session_id',
                           'revision', 'text', 'content_sha256'),
    'source_snapshots': ('id', 'owner_id', 'created_at', 'class_session_id',
                         'course_material_version_id', 'scope', 'snapshot_hash', 'coverage', 'source_ids'),
    'source_items': ('id', 'owner_id', 'created_at', 'snapshot_id', 'class_session_id',
                     'source_id', 'revision_id', 'source_key', 'frozen_text', 'source_hash',
                     'excerpt_sha256', 'excerpt_start', 'excerpt_end', 'token_bound'),
    'generation_plans': ('id', 'owner_id', 'created_at', 'class_session_id', 'snapshot_id',
                         'kind', 'plan_hash', 'request', 'config', 'expires_at',
                         'consumed_task_id', 'consumed_at'),
    'questions': ('id', 'owner_id', 'created_at', 'updated_at', 'revision', 'class_session_id',
                  'snapshot_id', 'task_id', 'prompt', 'answer_markdown', 'cited_source_ids', 'understood'),
}
QA_TABLES = frozenset(QA_COLUMNS)


def _table(name, columns, *, unique=(), foreign=(), indices=()):
    """Keep PostgreSQL's explicit key names for complete metadata parity."""
    table = sa.Table(name, metadata,
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('owner_id', UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('clock_timestamp()' if name != 'qa_sources' else 'transaction_timestamp()')),
        *columns,
        sa.ForeignKeyConstraint(['owner_id'], ['users.id']),
        *(sa.UniqueConstraint(*fields, name=key) for key, fields in unique),
        *(sa.ForeignKeyConstraint(local, remote, **options) for local, remote, options in foreign))
    for key, fields in indices:
        sa.Index(key, *(table.c[field] for field in fields))
    return table


qa_sources = _table('qa_sources', [
    sa.Column('class_session_id', UUID(as_uuid=True), nullable=False),
    sa.Column('kind', sa.Text, nullable=False),
    sa.Column('object_id', UUID(as_uuid=True), nullable=False),
    sa.Column('current_revision_id', UUID(as_uuid=True), nullable=False),
    sa.Column('origin', sa.Text, nullable=False),
    sa.Column('start_ms', sa.BigInteger), sa.Column('end_ms', sa.BigInteger),
    sa.Column('revoked_at', sa.DateTime(timezone=True)),
], unique=[('qa_sources_owner_id_id_key', ['owner_id', 'id']),
           ('qa_sources_owner_id_id_class_session_id_key', ['owner_id', 'id', 'class_session_id'])],
foreign=[(['owner_id', 'class_session_id'], ['class_sessions.owner_id', 'class_sessions.id'], {}),
         (['owner_id', 'id', 'current_revision_id'],
          ['qa_source_versions.owner_id', 'qa_source_versions.source_id', 'qa_source_versions.id'],
          dict(name='fk_qa_current_revision', deferrable=True, initially='DEFERRED', use_alter=True))],
indices=[('ix_qa_sources_class', ['owner_id', 'class_session_id', 'created_at', 'id'])])

qa_source_versions = _table('qa_source_versions', [
    sa.Column('source_id', UUID(as_uuid=True), nullable=False),
    sa.Column('class_session_id', UUID(as_uuid=True), nullable=False),
    sa.Column('revision', sa.BigInteger, nullable=False),
    sa.Column('text', sa.Text, nullable=False),
    sa.Column('content_sha256', sa.CHAR(64), nullable=False),
], unique=[('qa_source_versions_owner_id_id_key', ['owner_id', 'id']),
           ('qa_source_versions_owner_id_source_id_id_key', ['owner_id', 'source_id', 'id']),
           ('qa_source_versions_owner_id_source_id_revision_key', ['owner_id', 'source_id', 'revision'])],
foreign=[(['owner_id', 'source_id', 'class_session_id'],
          ['qa_sources.owner_id', 'qa_sources.id', 'qa_sources.class_session_id'], {})],
indices=[('ix_qa_versions_source', ['owner_id', 'source_id', 'revision'])])

source_snapshots = _table('source_snapshots', [
    sa.Column('class_session_id', UUID(as_uuid=True), nullable=False),
    sa.Column('course_material_version_id', UUID(as_uuid=True), nullable=False),
    sa.Column('scope', JSONB, nullable=False),
    sa.Column('snapshot_hash', sa.CHAR(64), nullable=False),
    sa.Column('coverage', JSONB, nullable=False),
    sa.Column('source_ids', ARRAY(UUID(as_uuid=True)), nullable=False),
], unique=[('source_snapshots_owner_id_id_key', ['owner_id', 'id']),
           ('source_snapshots_owner_id_id_class_session_id_key', ['owner_id', 'id', 'class_session_id'])],
foreign=[(['owner_id', 'class_session_id'], ['class_sessions.owner_id', 'class_sessions.id'], {}),
         (['owner_id', 'course_material_version_id'], ['course_material_versions.owner_id', 'course_material_versions.id'], {})])

source_items = _table('source_items', [
    sa.Column('snapshot_id', UUID(as_uuid=True), nullable=False),
    sa.Column('class_session_id', UUID(as_uuid=True), nullable=False),
    sa.Column('source_id', UUID(as_uuid=True), nullable=False),
    sa.Column('revision_id', UUID(as_uuid=True), nullable=False),
    sa.Column('source_key', sa.Text, nullable=False),
    sa.Column('frozen_text', sa.Text, nullable=False),
    sa.Column('source_hash', sa.CHAR(64), nullable=False),
    sa.Column('excerpt_sha256', sa.CHAR(64), nullable=False),
    sa.Column('excerpt_start', sa.Integer, nullable=False),
    sa.Column('excerpt_end', sa.Integer, nullable=False),
    sa.Column('token_bound', sa.Integer, nullable=False),
], unique=[('source_items_owner_id_id_key', ['owner_id', 'id']),
           ('source_items_owner_id_snapshot_id_source_key_key', ['owner_id', 'snapshot_id', 'source_key']),
           ('uq_qa_excerpt', ['owner_id', 'snapshot_id', 'source_id', 'revision_id', 'excerpt_start', 'excerpt_end'])],
foreign=[(['owner_id', 'snapshot_id', 'class_session_id'],
          ['source_snapshots.owner_id', 'source_snapshots.id', 'source_snapshots.class_session_id'], {}),
         (['owner_id', 'source_id', 'class_session_id'], ['qa_sources.owner_id', 'qa_sources.id', 'qa_sources.class_session_id'], {}),
         (['owner_id', 'source_id', 'revision_id'], ['qa_source_versions.owner_id', 'qa_source_versions.source_id', 'qa_source_versions.id'], {})],
indices=[('ix_source_items_source', ['owner_id', 'source_id'])])

generation_plans = _table('generation_plans', [
    sa.Column('class_session_id', UUID(as_uuid=True), nullable=False),
    sa.Column('snapshot_id', UUID(as_uuid=True), nullable=False),
    sa.Column('kind', sa.Text, nullable=False),
    sa.Column('plan_hash', sa.CHAR(64), nullable=False),
    sa.Column('request', JSONB, nullable=False),
    sa.Column('config', JSONB, nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_task_id', UUID(as_uuid=True)),
    sa.Column('consumed_at', sa.DateTime(timezone=True)),
], unique=[('generation_plans_owner_id_id_key', ['owner_id', 'id']),
           ('generation_plans_owner_id_id_class_session_id_key', ['owner_id', 'id', 'class_session_id']),
           ('generation_plans_owner_id_consumed_task_id_key', ['owner_id', 'consumed_task_id'])],
foreign=[(['owner_id', 'snapshot_id', 'class_session_id'],
          ['source_snapshots.owner_id', 'source_snapshots.id', 'source_snapshots.class_session_id'], {}),
         (['owner_id', 'consumed_task_id'], ['tasks.owner_id', 'tasks.id'],
          dict(deferrable=True, initially='DEFERRED', use_alter=True))],
indices=[('ix_qa_plans_owner_created', ['owner_id', 'created_at'])])

questions = _table('questions', [
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('clock_timestamp()')),
    sa.Column('revision', sa.BigInteger, nullable=False, server_default='1'),
    sa.Column('class_session_id', UUID(as_uuid=True), nullable=False),
    sa.Column('snapshot_id', UUID(as_uuid=True), nullable=False),
    sa.Column('task_id', UUID(as_uuid=True), nullable=False),
    sa.Column('prompt', sa.Text, nullable=False),
    sa.Column('answer_markdown', sa.Text),
    sa.Column('cited_source_ids', JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column('understood', sa.Boolean, nullable=False, server_default=sa.false()),
], unique=[('questions_owner_id_id_key', ['owner_id', 'id']),
           ('questions_owner_id_task_id_key', ['owner_id', 'task_id'])],
foreign=[(['owner_id', 'snapshot_id', 'class_session_id'],
          ['source_snapshots.owner_id', 'source_snapshots.id', 'source_snapshots.class_session_id'], {}),
         (['owner_id', 'task_id'], ['tasks.owner_id', 'tasks.id'], {})],
indices=[('ix_qa_questions_class_created', ['owner_id', 'class_session_id', 'created_at', 'id'])])
