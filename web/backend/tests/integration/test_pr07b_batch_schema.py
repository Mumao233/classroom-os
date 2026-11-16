"""Real PostgreSQL batch manifest authority, frozen aliases and task binding."""
import json
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from classroom_web.infrastructure.qa_repository import PgQaRepository
from classroom_web.infrastructure.review_generation import PgReviewGenerationRepository
from classroom_web.infrastructure.review_repository import PgReviewRepository
from classroom_web.infrastructure.task_schema import tables

from test_pr07a_sources import CONFIG, confirmation, seeded
from test_pr07b_versions import REQUEST, output


@pytest.fixture(autouse=True)
def isolate_batch_queue(pg, owners):
    yield
    with pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE tasks SET not_before=clock_timestamp()+interval '365 days' WHERE owner_id=ANY(:owners)"),
                           {'owners': [owners.a.owner_id, owners.b.owner_id]})


@pytest.fixture
def root_batch(pg, owners, tree):
    source = seeded(pg, owners.a, tree)
    with pg.db.transaction(owners.a) as tx:
        repository = PgReviewGenerationRepository(tx)
        plan = repository.prepare(tree.classroom.id, REQUEST, uuid4(), CONFIG)
        task = repository.execute(UUID(plan['id']), confirmation(plan), uuid4(), CONFIG)
        batch = tx.connection.execute(sa.text('''SELECT * FROM review_batches
            WHERE owner_id=:owner AND plan_id=:plan AND batch_index=0'''),
            {'owner': owners.a.owner_id, 'plan': UUID(plan['id'])}).mappings().one()
    return dict(source=source, plan=plan, task=task, row=dict(batch))


def insert_batch(tx, root, *, keys=None, index=97, snapshot=None, classroom=None):
    identity = uuid4()
    row = root['row']
    tx.connection.execute(sa.text('''INSERT INTO review_batches
        (id,owner_id,plan_id,class_session_id,batch_index,snapshot_id,source_keys)
        VALUES(:id,:owner,:plan,:classroom,:index,:snapshot,CAST(:keys AS jsonb))'''),
        dict(id=identity, owner=tx.catalog.owner_id, plan=row['plan_id'],
             classroom=classroom or row['class_session_id'], index=index,
             snapshot=snapshot or row['snapshot_id'], keys=json.dumps(keys if keys is not None else ['S1'])))
    return identity


def child_task(tx, root, *, schema_version='pr07b-batch-1'):
    child_plan, task = uuid4(), uuid4()
    tx.connection.execute(sa.text('''INSERT INTO generation_plans
        (id,owner_id,class_session_id,snapshot_id,kind,plan_hash,request,config,expires_at)
        SELECT :child,owner_id,class_session_id,snapshot_id,kind,plan_hash,request,config,expires_at
        FROM generation_plans WHERE owner_id=:owner AND id=:root'''),
        dict(child=child_plan, owner=tx.catalog.owner_id, root=root['row']['plan_id']))
    tx.connection.execute(tables['tasks'].insert().values(
        id=task, owner_id=tx.catalog.owner_id, class_session_id=root['row']['class_session_id'],
        parent_task_id=root['task'].id, kind='review_summary', state='queued',
        input_ref={'schema_version': 1}, schema_version=schema_version, plan_id=child_plan))
    tx.connection.execute(sa.text('''UPDATE generation_plans SET consumed_task_id=:task,consumed_at=clock_timestamp()
        WHERE owner_id=:owner AND id=:plan'''), dict(owner=tx.catalog.owner_id, plan=child_plan, task=task))
    return child_plan, task


def bind(tx, identity, child_plan, task):
    tx.connection.execute(sa.text('''UPDATE review_batches SET task_id=:task,child_plan_id=:child
        WHERE owner_id=:owner AND id=:id'''),
        dict(owner=tx.catalog.owner_id, id=identity, child=child_plan, task=task))


def test_root_manifest_binds_exact_root_task_and_plan(root_batch):
    batch = root_batch['row']
    assert batch['batch_index'] == 0
    assert batch['task_id'] == root_batch['task'].id
    assert batch['child_plan_id'] == batch['plan_id'] == UUID(root_batch['plan']['id'])
    assert batch['snapshot_id'] == UUID(root_batch['plan']['source_snapshot']['id'])
    assert batch['source_keys'] == ['S1']


@pytest.mark.parametrize('keys', [[], ['S1', 'S1'], ['S999'], [1], {'source': 'S1'}])
def test_manifest_source_keys_are_nonempty_unique_and_server_verified(pg, owners, root_batch, keys):
    with pytest.raises(sa.exc.DBAPIError) as error, pg.db.transaction(owners.a) as tx:
        insert_batch(tx, root_batch, keys=keys)
    assert error.value.orig.sqlstate == '23514'


@pytest.mark.parametrize('field,value', [
    ('source_keys', "'[\"S2\"]'::jsonb"),
    ('batch_index', '98'),
    ('created_at', "created_at+interval '1 second'"),
])
def test_manifest_identity_and_sources_are_immutable(pg, owners, root_batch, field, value):
    with pytest.raises(sa.exc.DBAPIError) as error, pg.db.transaction(owners.a) as tx:
        tx.connection.execute(sa.text(f'UPDATE review_batches SET {field}={value} WHERE id=:id'),
                              {'id': root_batch['row']['id']})
    assert error.value.orig.sqlstate == '23514'


def test_batch_binding_accepts_child_protocol_once(pg, owners, root_batch):
    with pg.db.transaction(owners.a) as tx:
        identity = insert_batch(tx, root_batch)
        child_plan, task = child_task(tx, root_batch)
        bind(tx, identity, child_plan, task)
        row = tx.connection.execute(sa.text('SELECT task_id,child_plan_id FROM review_batches WHERE id=:id'),
                                    {'id': identity}).one()
        assert row.task_id == task
        assert row.child_plan_id == child_plan
    with pytest.raises(sa.exc.DBAPIError) as error, pg.db.transaction(owners.a) as tx:
        bind(tx, identity, child_plan, task)
    assert error.value.orig.sqlstate == '23514'


@pytest.mark.parametrize('wrong', ['protocol', 'plan', 'root_task', 'half_pair'])
def test_batch_binding_rejects_wrong_protocol_plan_and_partial_pair(pg, owners, root_batch, wrong):
    with pytest.raises(sa.exc.DBAPIError) as error, pg.db.transaction(owners.a) as tx:
        identity = insert_batch(tx, root_batch)
        child_plan, task = child_task(tx, root_batch,
                                    schema_version='pr07b-1' if wrong=='protocol' else 'pr07b-batch-1')
        if wrong == 'plan':
            child_plan = root_batch['row']['plan_id']
        elif wrong == 'root_task':
            task = root_batch['task'].id
        elif wrong == 'half_pair':
            child_plan = None
        bind(tx, identity, child_plan, task)
    assert error.value.orig.sqlstate == '23514'


def test_manifest_is_owner_isolated_and_revoked_with_source(pg, owners, root_batch):
    with pg.db.transaction(owners.b) as tx:
        assert tx.connection.scalar(sa.text('SELECT count(*) FROM review_batches')) == 0
    with pytest.raises(sa.exc.DBAPIError) as foreign, pg.db.transaction(owners.b) as tx:
        insert_batch(tx, root_batch)
    assert foreign.value.orig.sqlstate in ('23514', '42501')
    with pg.db.transaction(owners.a) as tx:
        PgQaRepository(tx).revoke_source(root_batch['source'].source_id)
    with pg.db.transaction(owners.a) as tx:
        assert tx.connection.scalar(sa.text('SELECT count(*) FROM review_batches')) == 0


def test_manifest_rejects_a_different_classroom_snapshot(pg, owners, tree, root_batch):
    with pg.db.transaction(owners.a) as tx:
        classroom = tx.catalog.create_class_session(tree.course.id, 'Other synthetic classroom')
        PgQaRepository(tx).add_source(classroom.id, kind='transcript_segment', object_id=uuid4(),
                                     text='Other classroom source', start_ms=0, end_ms=1000)
        plan = PgReviewGenerationRepository(tx).prepare(classroom.id, REQUEST, uuid4(), CONFIG)
    with pytest.raises(sa.exc.DBAPIError) as error, pg.db.transaction(owners.a) as tx:
        insert_batch(tx, root_batch, snapshot=UUID(plan['source_snapshot']['id']), classroom=classroom.id)
    assert error.value.orig.sqlstate == '23514'


def test_manifest_delete_is_refused_even_for_migration_owner(pg, root_batch):
    with pytest.raises(sa.exc.DBAPIError) as error, pg.admin.begin() as connection:
        connection.execute(sa.text('DELETE FROM review_batches WHERE id=:id'), {'id': root_batch['row']['id']})
    assert error.value.orig.sqlstate == '23514'


def test_manifest_batch_index_is_unique_within_root_plan(pg, owners, root_batch):
    with pytest.raises(sa.exc.DBAPIError) as error, pg.db.transaction(owners.a) as tx:
        insert_batch(tx, root_batch, index=0)
    assert error.value.orig.sqlstate == '23505'


def test_child_task_cannot_publish_a_learning_revision_directly(pg, owners, root_batch):
    with pg.db.transaction(owners.a) as tx:
        result = PgReviewRepository(tx).publish(root_batch['task'], output(), root_batch['row']['snapshot_id'], REQUEST)
        _, task_id = child_task(tx, root_batch)
    with pytest.raises(sa.exc.DBAPIError) as error, pg.db.transaction(owners.a) as tx:
        tx.connection.execute(sa.text('''INSERT INTO review_revisions
            (id,owner_id,document_id,class_session_id,revision,kind,source_snapshot_id,task_id,artifact_key,
             body_markdown,structured_content,personal_note)
            SELECT :copy,owner_id,document_id,class_session_id,999,kind,source_snapshot_id,:child,artifact_key,
             body_markdown,structured_content,personal_note FROM review_revisions WHERE id=:original'''),
            dict(copy=uuid4(), child=task_id, original=UUID(result['revision_ids'][0])))
    assert error.value.orig.sqlstate == '23514'


def test_global_snapshot_accepts_more_than_twelve_complete_sources(pg, owners, tree):
    with pg.db.transaction(owners.a) as tx:
        sources = PgQaRepository(tx)
        for index in range(13):
            sources.add_source(tree.classroom.id, kind='transcript_segment', object_id=uuid4(),
                text=f'Synthetic unit {index}. '+('Classroom source content. '*42),
                start_ms=index*1000, end_ms=(index+1)*1000)
        plan = PgReviewGenerationRepository(tx).prepare(tree.classroom.id, REQUEST, uuid4(),
                                                       dict(CONFIG, input_token_budget=4000))
        snapshot = plan['source_snapshot']
        assert len(snapshot['items']) == 13
        assert {item['alias'] for item in snapshot['items']} == {f'S{index}' for index in range(1, 14)}
        batches = tx.connection.execute(sa.text('''SELECT source_keys FROM review_batches
            WHERE owner_id=:owner AND plan_id=:plan ORDER BY batch_index'''),
            {'owner': owners.a.owner_id, 'plan': UUID(plan['id'])}).scalars().all()
        assert len(batches) == plan['estimated_batches'] > 1
        aliases = [key for batch in batches for key in batch]
        assert len(aliases) == len(set(aliases)) == 13
        assert set(aliases) == {item['alias'] for item in snapshot['items']}


def test_large_alias_remains_bound_to_exact_frozen_source(pg, owners, root_batch):
    with pg.db.transaction(owners.a) as tx:
        snapshot = uuid4()
        tx.connection.execute(sa.text('''INSERT INTO source_snapshots
            (id,owner_id,class_session_id,course_material_version_id,scope,snapshot_hash,coverage,source_ids)
            SELECT :copy,owner_id,class_session_id,course_material_version_id,scope,snapshot_hash,coverage,source_ids
            FROM source_snapshots WHERE id=:original'''),
            dict(copy=snapshot, original=root_batch['row']['snapshot_id']))
        tx.connection.execute(sa.text('''INSERT INTO source_items
            (id,owner_id,snapshot_id,class_session_id,source_id,revision_id,source_key,frozen_text,source_hash,
             excerpt_sha256,excerpt_start,excerpt_end,token_bound)
            SELECT :copy,owner_id,:snapshot,class_session_id,source_id,revision_id,'S512',frozen_text,source_hash,
             excerpt_sha256,excerpt_start,excerpt_end,token_bound FROM source_items WHERE snapshot_id=:original'''),
            dict(copy=uuid4(), snapshot=snapshot, original=root_batch['row']['snapshot_id']))
        row = tx.connection.execute(sa.text('SELECT source_key,source_id FROM source_items WHERE snapshot_id=:id'),
                                    {'id': snapshot}).one()
        assert row.source_key == 'S512'
        assert row.source_id == root_batch['source'].source_id


def test_global_snapshot_rejects_more_than_512_source_ids(pg, owners, root_batch):
    with pytest.raises(sa.exc.DBAPIError) as error, pg.db.transaction(owners.a) as tx:
        tx.connection.execute(sa.text('''INSERT INTO source_snapshots
            (id,owner_id,class_session_id,course_material_version_id,scope,snapshot_hash,coverage,source_ids)
            SELECT :copy,owner_id,class_session_id,course_material_version_id,scope,snapshot_hash,coverage,:sources
            FROM source_snapshots WHERE id=:original'''), dict(copy=uuid4(),
                sources=[root_batch['source'].source_id]*513, original=root_batch['row']['snapshot_id']))
    assert error.value.orig.sqlstate == '23514'
