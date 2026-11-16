"""PR-07b metadata matching its additive literal PostgreSQL migration."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .qa_schema import _table


review_batches = _table('review_batches', [
    sa.Column('plan_id', UUID(as_uuid=True), nullable=False),
    sa.Column('class_session_id', UUID(as_uuid=True), nullable=False),
    sa.Column('batch_index', sa.Integer, nullable=False),
    sa.Column('snapshot_id', UUID(as_uuid=True), nullable=False),
    sa.Column('source_keys', JSONB, nullable=False),
    sa.Column('task_id', UUID(as_uuid=True)), sa.Column('child_plan_id', UUID(as_uuid=True)),
], unique=[('review_batches_owner_id_id_key', ['owner_id', 'id']),
           ('review_batches_owner_id_plan_id_batch_index_key', ['owner_id', 'plan_id', 'batch_index']),
           ('review_batches_owner_id_task_id_key', ['owner_id', 'task_id'])],
foreign=[(['owner_id', 'plan_id', 'class_session_id'], ['generation_plans.owner_id', 'generation_plans.id', 'generation_plans.class_session_id'], {}),
         (['owner_id', 'snapshot_id', 'class_session_id'], ['source_snapshots.owner_id', 'source_snapshots.id', 'source_snapshots.class_session_id'], {}),
         (['owner_id', 'task_id'], ['tasks.owner_id', 'tasks.id'], {}),
         (['owner_id', 'child_plan_id', 'class_session_id'], ['generation_plans.owner_id', 'generation_plans.id', 'generation_plans.class_session_id'], {})])


review_documents = _table('review_documents', [
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('clock_timestamp()')),
    sa.Column('revision', sa.BigInteger, nullable=False, server_default='1'),
    sa.Column('class_session_id', UUID(as_uuid=True), nullable=False),
    sa.Column('kind', sa.Text, nullable=False), sa.Column('title', sa.Text, nullable=False),
    sa.Column('origin_snapshot_id', UUID(as_uuid=True)), sa.Column('parent_document_id', UUID(as_uuid=True)),
    sa.Column('artifact_key', sa.Text, nullable=False),
    sa.Column('adopted_revision_id', UUID(as_uuid=True)), sa.Column('latest_personal_revision_id', UUID(as_uuid=True)),
    sa.Column('understood', sa.Boolean, nullable=False, server_default=sa.false()),
], unique=[('review_documents_owner_id_id_key', ['owner_id', 'id']),
           ('review_documents_owner_id_id_class_session_id_key', ['owner_id', 'id', 'class_session_id']),
           ('review_documents_owner_id_parent_document_id_artifact_key_key', ['owner_id', 'parent_document_id', 'artifact_key'])],
foreign=[(['owner_id', 'class_session_id'], ['class_sessions.owner_id', 'class_sessions.id'], {}),
         (['owner_id', 'origin_snapshot_id', 'class_session_id'], ['source_snapshots.owner_id', 'source_snapshots.id', 'source_snapshots.class_session_id'], {}),
         (['owner_id', 'parent_document_id', 'class_session_id'], ['review_documents.owner_id', 'review_documents.id', 'review_documents.class_session_id'], {}),
         (['owner_id', 'id', 'adopted_revision_id'], ['review_revisions.owner_id', 'review_revisions.document_id', 'review_revisions.id'],
          dict(name='fk_review_adopted', deferrable=True, initially='DEFERRED', use_alter=True)),
         (['owner_id', 'id', 'latest_personal_revision_id'], ['review_revisions.owner_id', 'review_revisions.document_id', 'review_revisions.id'],
          dict(name='fk_review_personal', deferrable=True, initially='DEFERRED', use_alter=True))],
indices=[('ix_review_documents_class', ['owner_id', 'class_session_id', 'created_at', 'id'])])

review_revisions = _table('review_revisions', [
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('clock_timestamp()')),
    sa.Column('document_id', UUID(as_uuid=True), nullable=False),
    sa.Column('class_session_id', UUID(as_uuid=True), nullable=False),
    sa.Column('revision', sa.BigInteger, nullable=False), sa.Column('kind', sa.Text, nullable=False),
    sa.Column('parent_revision_id', UUID(as_uuid=True)), sa.Column('source_snapshot_id', UUID(as_uuid=True)),
    sa.Column('raw_output_ref', UUID(as_uuid=True)), sa.Column('task_id', UUID(as_uuid=True)),
    sa.Column('artifact_key', sa.Text), sa.Column('draft_id', UUID(as_uuid=True)),
    sa.Column('body_markdown', sa.Text, nullable=False), sa.Column('structured_content', JSONB),
    sa.Column('personal_note', sa.Text, nullable=False, server_default=''),
], unique=[('review_revisions_owner_id_id_key', ['owner_id', 'id']),
           ('review_revisions_owner_id_document_id_id_key', ['owner_id', 'document_id', 'id']),
           ('review_revisions_owner_id_document_id_revision_key', ['owner_id', 'document_id', 'revision']),
           ('review_revisions_owner_id_task_id_artifact_key_key', ['owner_id', 'task_id', 'artifact_key'])],
foreign=[(['owner_id', 'document_id', 'class_session_id'], ['review_documents.owner_id', 'review_documents.id', 'review_documents.class_session_id'], {}),
         (['owner_id', 'document_id', 'parent_revision_id'], ['review_revisions.owner_id', 'review_revisions.document_id', 'review_revisions.id'], {}),
         (['owner_id', 'source_snapshot_id', 'class_session_id'], ['source_snapshots.owner_id', 'source_snapshots.id', 'source_snapshots.class_session_id'], {}),
         (['owner_id', 'task_id'], ['tasks.owner_id', 'tasks.id'], {}),
         (['owner_id', 'raw_output_ref'], ['provider_results.owner_id', 'provider_results.id'], {})],
indices=[('ix_review_revisions_document', ['owner_id', 'document_id', 'revision'])])

review_document_history = _table('review_document_history', [
    sa.Column('document_id', UUID(as_uuid=True), nullable=False),
    sa.Column('revision', sa.BigInteger, nullable=False), sa.Column('projection', JSONB, nullable=False),
], unique=[('review_document_history_owner_id_id_key', ['owner_id', 'id']),
           ('review_document_history_owner_id_document_id_revision_key', ['owner_id', 'document_id', 'revision'])],
foreign=[(['owner_id', 'document_id'], ['review_documents.owner_id', 'review_documents.id'], {})])

review_conflicts = _table('review_conflicts', [
    sa.Column('document_id', UUID(as_uuid=True), nullable=False),
    sa.Column('submitted_revision_id', UUID(as_uuid=True), nullable=False),
    sa.Column('current_revision_id', UUID(as_uuid=True)),
    sa.Column('current_document_revision', sa.BigInteger, nullable=False),
], unique=[('review_conflicts_owner_id_id_key', ['owner_id', 'id']),
           ('review_conflicts_owner_id_submitted_revision_id_key', ['owner_id', 'submitted_revision_id'])],
foreign=[(['owner_id', 'document_id'], ['review_documents.owner_id', 'review_documents.id'], {}),
         (['owner_id', 'document_id', 'submitted_revision_id'], ['review_revisions.owner_id', 'review_revisions.document_id', 'review_revisions.id'], {}),
         (['owner_id', 'document_id', 'current_revision_id'], ['review_revisions.owner_id', 'review_revisions.document_id', 'review_revisions.id'], {})])

REVIEW_TABLES = frozenset(('review_batches', 'review_documents', 'review_revisions', 'review_document_history', 'review_conflicts'))
