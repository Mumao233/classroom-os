"""Real PostgreSQL source lineage, immutable plans, revocation and concurrency."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import shutil
from uuid import UUID, uuid4

from alembic import command
import pytest
import sqlalchemy as sa

from classroom_web.domain.data import OwnerContext, Resource
from classroom_web.domain.qa import QaRejected
from classroom_web.domain.tasks import IdempotencyMismatch
from classroom_web.infrastructure.qa_repository import PgQaRepository
from classroom_web.infrastructure.qa_schema import QA_COLUMNS, QA_TABLES
from classroom_web.infrastructure.database import Database
from classroom_web.infrastructure.schema import metadata, tables as catalog_tables
from classroom_web.infrastructure.task_schema import tables as task_tables

from conftest import BACKEND, ROOT, migration_config
from test_task_migrations import previous_schema, snapshot as database_snapshot, upgrade, version


@pytest.fixture(autouse=True)
def isolate_source_task_queue(pg, owners):
    yield
    # Preserve this test's ledgers while removing only its owners from subsequent
    # shared-cluster scheduling, matching the existing task_db fixture boundary.
    with pg.admin.begin() as connection:
        connection.execute(sa.text('''UPDATE tasks SET not_before=clock_timestamp()+interval '365 days'
            WHERE owner_id=ANY(:owners)'''), {'owners': [owners.a.owner_id, owners.b.owner_id]})


CONFIG = dict(destination=dict(provider='fake', model='synthetic', region='local', configuration_revision='1'),
              price_id=None, price_version='synthetic-no-charge', maximum_cost_microyuan=0,
              input_token_budget=8192, max_output_tokens=1024, enabled=True, billing_required=False)
REQUEST = dict(kind='ai_question', scope='all', question='Explain synthetic momentum.',
               seen_sources=[], include_images=False, accept_partial_scope=True)


def confirmation(plan, **overrides):
    return dict(plan_sha256=plan['plan_sha256'], confirmed_destination_revision='1',
                confirm_send=True, confirm_images=False, confirm_original_snapshot=False,
                confirm_partial_scope=True, **overrides)


def seeded(pg, owner, tree, text='Synthetic momentum equals mass times velocity.'):
    with pg.db.transaction(owner) as tx:
        repo = PgQaRepository(tx)
        source = repo.add_source(tree.classroom.id, kind='transcript_segment', object_id=uuid4(),
                                 text=text, start_ms=1000, end_ms=5000)
    return source


def prepared(pg, owner, tree, request=None):
    with pg.db.transaction(owner) as tx:
        return PgQaRepository(tx).prepare(tree.classroom.id, request or REQUEST, uuid4(), CONFIG)


def test_qa_table_inventory_columns_and_force_rls(pg):
    inspector = sa.inspect(pg.admin)
    for name, columns in QA_COLUMNS.items():
        assert {item['name'] for item in inspector.get_columns(name)} == set(columns)
    with pg.admin.connect() as connection:
        rows = connection.execute(sa.text('''SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class
            WHERE relname=ANY(:names)'''), {'names': list(QA_TABLES)}).all()
    assert len(rows) == len(QA_TABLES)
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in rows)


def test_source_plan_snapshot_and_immutable_version(pg, owners, tree):
    source = seeded(pg, owners.a, tree)
    plan = prepared(pg, owners.a, tree)
    frozen = plan['source_snapshot']
    assert frozen['items'][0]['revision_id'] == str(source.revision_id)
    with pg.db.transaction(owners.a) as tx:
        revised = PgQaRepository(tx).revise_source(source.source_id, 'Corrected synthetic momentum explanation.')
    assert revised.revision_id != source.revision_id
    with pg.db.transaction(owners.a) as tx:
        current = PgQaRepository(tx).plan(UUID(plan['id']))
    assert current['source_status'] == 'changed'
    assert current['source_snapshot'] == frozen
    with pytest.raises(sa.exc.IntegrityError):
        with pg.admin.begin() as connection:
            connection.execute(sa.text('UPDATE qa_source_versions SET text=:text WHERE id=:id'),
                               {'text': 'silently replaced', 'id': source.revision_id})


def test_changed_source_requires_original_snapshot_confirmation(pg, owners, tree):
    source = seeded(pg, owners.a, tree)
    plan = prepared(pg, owners.a, tree)
    with pg.db.transaction(owners.a) as tx:
        PgQaRepository(tx).revise_source(source.source_id, 'A later correction.')
    with pytest.raises(QaRejected, match='CONFIRMATION_REQUIRED'):
        with pg.db.transaction(owners.a) as tx:
            PgQaRepository(tx).execute(UUID(plan['id']), confirmation(plan), uuid4(), CONFIG)
    body = confirmation(plan)
    body['confirm_original_snapshot'] = True
    with pg.db.transaction(owners.a) as tx:
        task = PgQaRepository(tx).execute(UUID(plan['id']), body, uuid4(), CONFIG)
        inputs = PgQaRepository(tx).worker_input(task.id)
        assert inputs['selection'].entries[0].source.revision_id == source.revision_id


def test_empty_classroom_and_unsupported_scope_are_explicit(pg, owners, tree):
    with pytest.raises(QaRejected, match='CONTEXT_EMPTY'):
        prepared(pg, owners.a, tree)
    seeded(pg, owners.a, tree)
    with pytest.raises(QaRejected, match='UNSUPPORTED_SCOPE'):
        prepared(pg, owners.a, tree, dict(REQUEST, scope='slides'))


def test_foreign_owner_and_foreign_class_sources_are_rejected(pg, owners, tree):
    source = seeded(pg, owners.a, tree)
    plan = prepared(pg, owners.a, tree)
    with pg.db.transaction(owners.b) as tx:
        for name in QA_TABLES:
            assert tx.connection.execute(sa.text(f'SELECT * FROM {name}')).all() == []
        with pytest.raises(QaRejected):
            PgQaRepository(tx).snapshot(UUID(plan['source_snapshot']['id']))
    with pg.db.transaction(owners.a) as tx:
        other = tx.catalog.create_class_session(tree.course.id, 'Another synthetic classroom')
    reference = dict(kind=source.kind, id=str(source.source_id), revision_id=str(source.revision_id),
                     content_sha256=source.content_sha256)
    with pytest.raises(QaRejected, match='SOURCE_CHANGED'):
        with pg.db.transaction(owners.a) as tx:
            PgQaRepository(tx).prepare(other.id, dict(REQUEST, seen_sources=[reference]), uuid4(), CONFIG)


def test_revocation_hides_snapshot_plan_items_task_and_question(pg, owners, tree):
    source = seeded(pg, owners.a, tree)
    plan = prepared(pg, owners.a, tree)
    with pg.db.transaction(owners.a) as tx:
        repo = PgQaRepository(tx)
        task = repo.execute(UUID(plan['id']), confirmation(plan), uuid4(), CONFIG)
        question_id = UUID(repo.questions(tree.classroom.id)['items'][0]['id'])
    with pg.db.transaction(owners.a) as tx:
        PgQaRepository(tx).revoke_source(source.source_id)
    with pg.db.transaction(owners.a) as tx:
        for name in ('source_snapshots', 'source_items', 'generation_plans', 'questions'):
            assert tx.connection.execute(sa.text(f'SELECT * FROM {name}')).all() == []
        assert tx.connection.execute(sa.text('SELECT * FROM tasks WHERE id=:id'), {'id': task.id}).all() == []
        with pytest.raises(QaRejected):
            PgQaRepository(tx).question(question_id)


@pytest.mark.parametrize('scope', ['course', 'semester', 'class_session'])
def test_ancestor_tombstone_revokes_snapshot(pg, owners, tree, scope):
    seeded(pg, owners.a, tree)
    plan = prepared(pg, owners.a, tree)
    target = {'course': tree.course, 'semester': tree.semester, 'class_session': tree.classroom}[scope]
    with pg.db.transaction(owners.a) as tx:
        tx.tombstones.revoke(Resource(scope), target.id, target.revision, 1)
    with pg.db.transaction(owners.a) as tx:
        with pytest.raises(QaRejected):
            PgQaRepository(tx).snapshot(UUID(plan['source_snapshot']['id']))


def test_moved_classroom_retains_original_material_permissions(pg, owners, tree):
    seeded(pg, owners.a, tree)
    plan = prepared(pg, owners.a, tree)
    with pg.db.transaction(owners.a) as tx:
        destination = tx.catalog.create_course(tree.semester.id, 'Synthetic destination')
        tx.catalog.move_class_session(tree.classroom.id, 1, destination.id)
        original = tx.catalog.get_course(tree.course.id)
        tx.tombstones.revoke(Resource.COURSE, original.id, original.revision, 2)
    with pg.db.transaction(owners.a) as tx:
        with pytest.raises(QaRejected):
            PgQaRepository(tx).plan(UUID(plan['id']))


def test_prepare_and_execute_idempotency_and_plan_once(pg, owners, tree):
    seeded(pg, owners.a, tree)
    key = uuid4()
    with pg.db.transaction(owners.a) as tx:
        first = PgQaRepository(tx).prepare(tree.classroom.id, REQUEST, key, CONFIG)
    with pg.db.transaction(owners.a) as tx:
        assert PgQaRepository(tx).prepare(tree.classroom.id, REQUEST, key, CONFIG) == first
    with pytest.raises(IdempotencyMismatch):
        with pg.db.transaction(owners.a) as tx:
            PgQaRepository(tx).prepare(tree.classroom.id, dict(REQUEST, question='Different'), key, CONFIG)
    def consume():
        with pg.db.transaction(owners.a) as tx:
            return PgQaRepository(tx).execute(UUID(first['id']), confirmation(first), uuid4(), CONFIG).id
    with ThreadPoolExecutor(max_workers=2) as pool:
        identifiers = list(pool.map(lambda _: consume(), range(2)))
    assert identifiers[0] == identifiers[1]
    with pg.db.transaction(owners.a) as tx:
        assert len(PgQaRepository(tx).questions(tree.classroom.id)['items']) == 1


def test_long_middle_excerpt_multiple_windows_and_hashes(pg, owners, tree):
    body = 'x ' * 1500 + 'Important synthetic momentum equals mass times velocity. ' + 'y ' * 1500
    source = seeded(pg, owners.a, tree, text=body)
    plan = prepared(pg, owners.a, tree)
    items = plan['source_snapshot']['items']
    assert any('Important synthetic momentum' in item['excerpt'] for item in items)
    assert all(item['content_sha256'] == source.content_sha256 for item in items)
    for item in items:
        assert item['excerpt'] == body[item['excerpt_start']:item['excerpt_end']]
        assert hashlib.sha256(item['excerpt'].encode()).hexdigest() == item['excerpt_sha256']


def test_reference_validation_rejects_forged_alias(pg, owners, tree):
    seeded(pg, owners.a, tree)
    plan = prepared(pg, owners.a, tree)
    with pg.db.transaction(owners.a) as tx:
        repo = PgQaRepository(tx)
        task = repo.execute(UUID(plan['id']), confirmation(plan), uuid4(), CONFIG)
        validate = repo.validator(task.id)
        with pytest.raises(ValueError):
            validate(json.dumps(dict(answer_markdown='Unsupported', source_ids=['S99'])))
        value = validate(json.dumps(dict(answer_markdown='Supported', source_ids=['S1'])))
        assert value['source_ids'] == ['S1']


def test_material_source_binds_exact_frozen_text_and_has_no_fake_time(pg, owners, tree):
    with pg.db.transaction(owners.a) as tx:
        material = tx.catalog.update_material(tree.course.id, 1, syllabus='Synthetic course material.',
                                              glossary=[], language='auto')
        classroom = tx.catalog.create_class_session(tree.course.id, 'Material classroom')
        repo = PgQaRepository(tx)
        with pytest.raises(QaRejected):
            repo.add_source(classroom.id, kind='course_material_version', object_id=material.id,
                            text='Forged material.')
        source = repo.add_source(classroom.id, kind='course_material_version', object_id=material.id,
                                 text=repo.material_text(material))
        plan = repo.prepare(classroom.id, REQUEST, uuid4(), CONFIG)
    assert source.origin == 'material'
    assert plan['source_snapshot']['start_ms'] is None
    assert plan['source_snapshot']['end_ms'] is None


def test_material_source_revocation_uses_metadata_only_role_and_hides_task(pg, owners, tree):
    with pg.db.transaction(owners.a) as tx:
        material = tx.catalog.update_material(tree.course.id, 1, syllabus='Material revocation fixture.',
                                              glossary=[], language='auto')
        classroom = tx.catalog.create_class_session(tree.course.id, 'Material revoke classroom')
        repo = PgQaRepository(tx)
        source = repo.add_source(classroom.id, kind='course_material_version', object_id=material.id,
                                 text=repo.material_text(material))
        plan = repo.prepare(classroom.id, REQUEST, uuid4(), CONFIG)
        task = repo.execute(UUID(plan['id']), confirmation(plan), uuid4(), CONFIG)
    with pg.db.transaction(owners.a) as tx:
        PgQaRepository(tx).revoke_source(source.source_id)
    with pg.db.transaction(owners.a) as tx:
        assert tx.connection.execute(sa.text('SELECT id FROM tasks WHERE id=:id'), {'id': task.id}).all() == []
        with pytest.raises(QaRejected, match='SOURCE_UNAVAILABLE'):
            PgQaRepository(tx).snapshot(UUID(plan['source_snapshot']['id']))


def test_disabled_or_unbudgeted_nonfake_configuration_fails_closed(pg, owners, tree):
    seeded(pg, owners.a, tree)
    for config in (dict(CONFIG, enabled=False), dict(CONFIG, maximum_cost_microyuan=1),
                   dict(CONFIG, destination=dict(CONFIG['destination'], provider='real'))):
        with pytest.raises(QaRejected, match='SERVICE_DISABLED'):
            with pg.db.transaction(owners.a) as tx:
                PgQaRepository(tx).prepare(tree.classroom.id, REQUEST, uuid4(), config)


def test_four_active_requests_are_enforced_atomically(pg, owners, tree):
    seeded(pg, owners.a, tree)
    plans = [prepared(pg, owners.a, tree) for _ in range(5)]
    for plan in plans[:4]:
        with pg.db.transaction(owners.a) as tx:
            PgQaRepository(tx).execute(UUID(plan['id']), confirmation(plan), uuid4(), CONFIG)
    with pytest.raises(QaRejected, match='RATE_LIMITED'):
        with pg.db.transaction(owners.a) as tx:
            PgQaRepository(tx).execute(UUID(plans[4]['id']), confirmation(plans[4]), uuid4(), CONFIG)


def test_snapshot_cannot_mix_same_owner_classrooms_at_database_boundary(pg, owners, tree):
    source = seeded(pg, owners.a, tree)
    with pg.db.transaction(owners.a) as tx:
        other = tx.catalog.create_class_session(tree.course.id, 'A distinct synthetic class')
    with pytest.raises(sa.exc.IntegrityError):
        with pg.db.transaction(owners.a) as tx:
            tx.connection.execute(sa.text('''INSERT INTO source_snapshots(id,owner_id,class_session_id,
                course_material_version_id,scope,snapshot_hash,coverage,source_ids)
                VALUES(:id,:owner,:classroom,:material,'{}',:digest,'{}',:sources)'''),
                dict(id=uuid4(), owner=owners.a.owner_id, classroom=other.id,
                     material=tree.material.id, digest='a'*64, sources=[source.source_id]))


def test_source_revoke_denies_original_minimized_prepare_receipt(pg, owners, tree):
    source = seeded(pg, owners.a, tree)
    key = uuid4()
    with pg.db.transaction(owners.a) as tx:
        PgQaRepository(tx).prepare(tree.classroom.id, REQUEST, key, CONFIG)
    with pg.db.transaction(owners.a) as tx:
        PgQaRepository(tx).revoke_source(source.source_id)
    with pytest.raises(QaRejected, match='SOURCE_UNAVAILABLE'):
        with pg.db.transaction(owners.a) as tx:
            PgQaRepository(tx).prepare(tree.classroom.id, REQUEST, key, CONFIG)


def test_source_revocation_function_is_owner_scoped_monotonic_and_metadata_only(pg, owners, tree):
    source = seeded(pg, owners.a, tree)
    with pg.db.transaction(owners.b) as tx:
        assert tx.connection.scalar(sa.text('SELECT classroom_web_revoke_qa_source(:id)'),
                                    {'id': source.source_id}) is False
    with pg.db.transaction(owners.a) as tx:
        PgQaRepository(tx).revoke_source(source.source_id)
    with pg.db.transaction(owners.a) as tx:
        assert tx.connection.scalar(sa.text('SELECT classroom_web_revoke_qa_source(:id)'),
                                    {'id': source.source_id}) is False
        assert tx.connection.execute(sa.text('UPDATE qa_sources SET revoked_at=NULL WHERE id=:id'),
                                      {'id': source.source_id}).rowcount == 0
        assert not tx.connection.scalar(sa.text("SELECT pg_has_role(current_user,'classroom_web_qa_revoker','MEMBER')"))
        assert not tx.connection.scalar(sa.text("SELECT has_table_privilege('classroom_web_qa_revoker','qa_source_versions','SELECT')"))
        assert not tx.connection.scalar(sa.text("SELECT has_column_privilege('classroom_web_qa_revoker','qa_sources','object_id','SELECT')"))
    with pytest.raises(sa.exc.IntegrityError):
        with pg.admin.begin() as connection:
            connection.execute(sa.text('UPDATE qa_sources SET revoked_at=NULL WHERE id=:id'), {'id': source.source_id})


def test_plan_expiry_is_checked_before_task_or_reservation(pg, owners, tree):
    seeded(pg, owners.a, tree)
    plan = prepared(pg, owners.a, tree)
    # The service clock is injected locally for this check. Stored plan contents
    # remain immutable, and the test never disables their database guard.
    with pytest.raises(QaRejected, match='PLAN_EXPIRED'):
        with pg.db.transaction(owners.a) as tx:
            from datetime import datetime, timedelta
            repo = PgQaRepository(tx)
            repo._now = lambda: datetime.fromisoformat(plan['expires_at']) + timedelta(seconds=1)
            repo.execute(UUID(plan['id']), confirmation(plan), uuid4(), CONFIG)
    with pg.db.transaction(owners.a) as tx:
        assert tx.connection.execute(sa.text('SELECT id FROM tasks WHERE owner_id=:owner'),
                                     {'owner': owners.a.owner_id}).all() == []


def test_task_plan_rejects_same_owner_wrong_classroom_at_commit(pg, owners, tree):
    seeded(pg, owners.a, tree)
    plan = prepared(pg, owners.a, tree)
    with pg.db.transaction(owners.a) as tx:
        other = tx.catalog.create_class_session(tree.course.id, 'Another task destination')
    with pytest.raises(sa.exc.IntegrityError) as caught:
        with pg.db.transaction(owners.a) as tx:
            tx.tasks.enqueue('ai_question', {'schema_version': 1}, op_id=uuid4(),
                class_session_id=other.id, schema_version='pr07a-1', plan_id=UUID(plan['id']))
    assert caught.value.orig.sqlstate == '23503'
    assert caught.value.orig.diag.constraint_name == 'fk_tasks_qa_plan_lineage'


def test_question_pagination_is_ordered_signed_and_filter_bound(pg, owners, tree):
    seeded(pg, owners.a, tree)
    for _ in range(3):
        plan = prepared(pg, owners.a, tree)
        with pg.db.transaction(owners.a) as tx:
            PgQaRepository(tx).execute(UUID(plan['id']), confirmation(plan), uuid4(), CONFIG)
    with pg.db.transaction(owners.a) as tx:
        other = tx.catalog.create_class_session(tree.course.id, 'Cursor destination')
    prior_secret = pg.db.cursor_secret
    pg.db.cursor_secret = None
    try:
        with pytest.raises(QaRejected, match='SERVICE_DISABLED'):
            with pg.db.transaction(owners.a) as tx:
                PgQaRepository(tx).questions(tree.classroom.id, limit=1)
        pg.db.cursor_secret = b'synthetic-pr07a-cursor-key-32bytes-minimum'
        with pg.db.transaction(owners.a) as tx:
            first = PgQaRepository(tx).questions(tree.classroom.id, limit=1)
        cursor = first['next_cursor']
        assert cursor
        for context, class_id, token in (
                (owners.b, None, cursor), (owners.a, other.id, cursor),
                (owners.a, None, cursor),
                (owners.a, tree.classroom.id, ('A' if cursor[0] != 'A' else 'B')+cursor[1:])):
            with pytest.raises(QaRejected, match='VALIDATION_FAILED'):
                with pg.db.transaction(context) as tx:
                    PgQaRepository(tx).questions(class_id, limit=1, cursor=token)
        identifiers = [first['items'][0]['id']]
        while cursor:
            with pg.db.transaction(owners.a) as tx:
                page = PgQaRepository(tx).questions(tree.classroom.id, limit=1, cursor=cursor)
            identifiers.extend(item['id'] for item in page['items'])
            cursor = page['next_cursor']
        with pg.admin.connect() as connection:
            expected = [str(value) for value in connection.execute(sa.text('''SELECT id FROM questions
                WHERE owner_id=:owner AND class_session_id=:classroom ORDER BY created_at,id'''),
                {'owner': owners.a.owner_id, 'classroom': tree.classroom.id}).scalars()]
        assert identifiers == expected
        assert len(set(identifiers)) == 3
    finally:
        pg.db.cursor_secret = prior_secret


def test_upgrade_from_0009_preserves_catalog_and_existing_task_and_is_repeatable(previous_schema):
    database = previous_schema
    upgrade(database, '0009_private_media')
    runtime = Database(database.runtime_url.render_as_string(hide_password=False))
    try:
        with runtime.transaction(OwnerContext(database.owner_id)) as tx:
            old_task = tx.tasks.enqueue('export', {'schema_version': 1}, op_id=uuid4(),
                                         class_session_id=database.classroom_id)
        before = database_snapshot(database, catalog_tables | task_tables)
        before_tables = set(sa.inspect(database.engine).get_table_names())
        upgrade(database, 'pr07a_001')
        upgrade(database, 'pr07a_001')
        assert version(database) == 'pr07a_001'
        assert database_snapshot(database, catalog_tables | task_tables) == before
        assert set(sa.inspect(database.engine).get_table_names()) == before_tables | QA_TABLES
        with runtime.transaction(OwnerContext(database.owner_id)) as tx:
            assert tx.tasks.get(old_task.id) == old_task
            assert tx.catalog.get_class_session(database.classroom_id).course_id == database.course_id
    finally:
        runtime.dispose()


def test_failed_later_migration_and_refused_downgrade_preserve_qa_data(previous_schema):
    database = previous_schema
    upgrade(database, 'pr07a_001')
    runtime = Database(database.runtime_url.render_as_string(hide_password=False))
    try:
        with runtime.transaction(OwnerContext(database.owner_id)) as tx:
            repo = PgQaRepository(tx)
            repo.add_source(database.classroom_id, kind='transcript_segment', object_id=uuid4(),
                            text='Durable synthetic source for migration rollback.', start_ms=0, end_ms=1000)
            plan = repo.prepare(database.classroom_id, REQUEST, uuid4(), CONFIG)
            task = repo.execute(UUID(plan['id']), confirmation(plan), uuid4(), CONFIG)
        selected = catalog_tables | task_tables | {name: metadata.tables[name] for name in QA_TABLES}
        before = database_snapshot(database, selected)
        scratch = ROOT / 'work/pr07a/migration-failure' / uuid4().hex
        scratch.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(BACKEND / 'migrations', scratch)
        (scratch / 'versions/zz_pr07a_failure.py').write_text(
            "from alembic import op\nrevision='zz_pr07a_failure'\ndown_revision='pr07a_001'\n"
            "branch_labels=None\ndepends_on=None\ndef upgrade():\n"
            "    op.execute('CREATE TABLE pr07a_failed_ddl (id integer)')\n"
            f"    op.execute(\"UPDATE tasks SET priority=99,revision=revision+1 WHERE id='{task.id}'\")\n"
            "    op.execute('SELECT 1/0')\ndef downgrade():\n    raise RuntimeError('synthetic failure')\n",
            encoding='utf8')
        with pytest.raises(sa.exc.DBAPIError) as caught:
            upgrade(database, 'zz_pr07a_failure', scratch)
        assert caught.value.orig.sqlstate == '22012'
        assert version(database) == 'pr07a_001'
        assert database_snapshot(database, selected) == before
        assert 'pr07a_failed_ddl' not in sa.inspect(database.engine).get_table_names()
        cfg = migration_config(database.url.render_as_string(hide_password=False))
        with pytest.raises(RuntimeError, match='preserve source snapshots'):
            with database.engine.connect() as connection:
                cfg.attributes['connection'] = connection
                command.downgrade(cfg, '0009_private_media')
        assert version(database) == 'pr07a_001'
        assert database_snapshot(database, selected) == before
    finally:
        runtime.dispose()
