"""Real PostgreSQL review lineage, version CAS, conflict retention and RLS."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from classroom_web.domain.data import OwnerContext, Resource, RevisionConflict
from classroom_web.domain.qa import QaRejected
from classroom_web.domain.tasks import IdempotencyMismatch
from classroom_web.infrastructure.qa_repository import PgQaRepository
from classroom_web.infrastructure.review_generation import PgReviewGenerationRepository
from classroom_web.infrastructure.review_repository import PgReviewRepository
from classroom_web.infrastructure.review_schema import REVIEW_TABLES

from test_pr07a_sources import CONFIG, confirmation, seeded
from test_task_migrations import previous_schema, upgrade, version, snapshot as database_snapshot
from classroom_web.infrastructure.schema import tables as catalog_tables
from classroom_web.infrastructure.task_schema import tables as task_tables


REQUEST = dict(kind='review_summary', scope='all', seen_sources=[], include_images=False,
               accept_partial_scope=True, card_types=['concept'], english_answer=False)


@pytest.fixture(autouse=True)
def isolate_review_queue(pg, owners):
    yield
    with pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE tasks SET not_before=clock_timestamp()+interval '365 days' WHERE owner_id=ANY(:owners)"),
                           {'owners': [owners.a.owner_id, owners.b.owner_id]})


def output(alias='S1', *, answer='Momentum is the product of mass and velocity.'):
    return dict(schema_version=1, cards=[dict(type='concept', title='Momentum', front='What is momentum?',
        back=answer, supplement='', english='', english_meaning='', source_ids=[alias], missing=[])],
        notes=[dict(section='structure', title='Momentum', body=answer, supplement='', source_ids=[alias], missing=[])])


def prepare_review(pg, owner, tree, *, request=None):
    with pg.db.transaction(owner) as tx:
        repo = PgReviewGenerationRepository(tx)
        plan = repo.prepare(tree.classroom.id, request or REQUEST, uuid4(), CONFIG)
        task = repo.execute(UUID(plan['id']), confirmation(plan), uuid4(), CONFIG)
    return plan, task


def generate(pg, owners, tree):
    source = seeded(pg, owners.a, tree)
    plan, task = prepare_review(pg, owners.a, tree)
    with pg.db.transaction(owners.a) as tx:
        result = PgReviewRepository(tx).publish(task, output(), UUID(plan['source_snapshot']['id']), REQUEST)
    return source, plan, task, result


def personal(document, *, parent=None, text='My durable personal draft.', draft=None):
    return dict(base_revision=document['revision'], base_content_revision_id=parent,
                draft_id=str(draft or uuid4()), body_markdown=text, personal_note='Personal interpretation.')


def test_review_inventory_rls_and_minimum_grants(pg):
    with pg.admin.connect() as connection:
        rows = connection.execute(sa.text('''SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class
            WHERE relname=ANY(:names)'''), {'names': list(REVIEW_TABLES)}).all()
        assert len(rows) == len(REVIEW_TABLES)
        assert all(row.relrowsecurity and row.relforcerowsecurity for row in rows)
        for table in REVIEW_TABLES:
            assert not connection.scalar(sa.text("SELECT has_table_privilege('classroom_web_runtime',:table,'DELETE')"), {'table': table})
        for table in REVIEW_TABLES-{'review_documents', 'review_batches'}:
            assert not connection.scalar(sa.text("SELECT has_table_privilege('classroom_web_runtime',:table,'UPDATE')"), {'table': table})


def test_candidate_and_cards_persist_without_adoption(pg, owners, tree):
    _, plan, task, result = generate(pg, owners, tree)
    with pg.db.transaction(owners.a) as tx:
        repo = PgReviewRepository(tx)
        summary = repo.document(UUID(result['summary_document_id']))
        assert summary['kind'] == 'summary'
        assert summary['adopted_revision_id'] is None
        assert summary['latest_personal_revision_id'] is None
        assert summary['understood'] is False
        assert len(summary['candidate_revision_ids']) == 1
        revision = repo.revision(UUID(summary['candidate_revision_ids'][0]))
        assert revision['structured_content'] == output()
        assert revision['source_snapshot_id'] == plan['source_snapshot']['id']
        assert revision['kind'] == 'ai_candidate'
        assert repo.publish(task, output(), UUID(plan['source_snapshot']['id']), REQUEST) == result
        assert len(repo.documents(class_session_id=tree.classroom.id)['items']) == 2
        assert repo.document(UUID(result['document_ids'][1]))['kind'] == 'card'


def test_personal_edits_adoption_and_regeneration_preserve_each_pointer(pg, owners, tree):
    _, _, _, result = generate(pg, owners, tree)
    identity = UUID(result['summary_document_id'])
    with pg.db.transaction(owners.a) as tx:
        repo = PgReviewRepository(tx)
        document = repo.document(identity)
        ai_id = document['candidate_revision_ids'][0]
        saved = repo.write_personal(identity, personal(document, parent=ai_id), uuid4())
        current = repo.document(identity)
        adopted = repo.adopt(identity, dict(base_revision=current['revision'], revision_id=saved['id']), uuid4())
        observed = deepcopy(adopted)
    request = dict(REQUEST, target_document_id=str(identity), base_revision=observed['revision'])
    plan, task = prepare_review(pg, owners.a, tree, request=request)
    with pg.db.transaction(owners.a) as tx:
        repo = PgReviewRepository(tx)
        regenerated = repo.publish(task, output(answer='The source defines momentum as mass multiplied by velocity.'),
                                   UUID(plan['source_snapshot']['id']), request)
        latest = repo.document(identity)
        assert regenerated['summary_document_id'] == str(identity)
        assert latest['adopted_revision_id'] == saved['id']
        assert latest['latest_personal_revision_id'] == saved['id']
        assert latest['understood'] is False
        assert len(latest['candidate_revision_ids']) == 2
        assert repo.revision(UUID(ai_id))['structured_content'] == output()
        assert repo.revision(UUID(saved['id']))['body_markdown'] == 'My durable personal draft.'
        assert repo.document(identity, revision=observed['revision']) == observed
        assert set(regenerated['document_ids']) == set(result['document_ids'])


def test_conflicting_personal_save_commits_submitted_branch_and_idempotent_receipt(pg, owners, tree):
    _, _, _, result = generate(pg, owners, tree)
    identity = UUID(result['summary_document_id'])
    with pg.db.transaction(owners.a) as tx:
        repo = PgReviewRepository(tx)
        document = repo.document(identity)
        parent = document['candidate_revision_ids'][0]
        first = repo.write_personal(identity, personal(document, parent=parent, text='First device'), uuid4())
    body = personal(document, parent=parent, text='Second device')
    operation = uuid4()
    with pg.db.transaction(owners.a) as tx:
        conflict = PgReviewRepository(tx).write_personal(identity, body, operation)
        assert conflict['submitted_revision']['body_markdown'] == 'Second device'
        assert UUID(conflict['conflict_id']).version == 4
    with pg.db.transaction(owners.a) as tx:
        repo = PgReviewRepository(tx)
        assert repo.document(identity)['latest_personal_revision_id'] == first['id']
        assert repo.write_personal(identity, body, operation) == conflict
        assert len(repo.revisions(identity)['items']) == 3
    with pytest.raises(IdempotencyMismatch), pg.db.transaction(owners.a) as tx:
        PgReviewRepository(tx).write_personal(identity, dict(body, body_markdown='Different retry'), operation)


def test_concurrent_adoption_has_exactly_one_cas_winner(pg, owners, tree):
    _, _, _, result = generate(pg, owners, tree)
    identity = UUID(result['summary_document_id'])
    with pg.db.transaction(owners.a) as tx:
        document = PgReviewRepository(tx).document(identity)
    body = dict(base_revision=document['revision'], revision_id=document['candidate_revision_ids'][0])

    def adopt():
        try:
            with pg.db.transaction(owners.a) as tx:
                return PgReviewRepository(tx).adopt(identity, body, uuid4())['revision']
        except RevisionConflict:
            return 'conflict'

    with ThreadPoolExecutor(max_workers=2) as workers:
        values = list(workers.map(lambda _: adopt(), range(2)))
    assert values.count('conflict') == 1
    assert values.count(document['revision']+1) == 1


def test_editor_draft_id_can_save_further_content_with_a_new_operation(pg, owners, tree):
    _, _, _, result = generate(pg, owners, tree)
    identity = UUID(result['summary_document_id'])
    draft = uuid4()
    with pg.db.transaction(owners.a) as tx:
        repo = PgReviewRepository(tx)
        document = repo.document(identity)
        first = repo.write_personal(identity, personal(document, parent=document['candidate_revision_ids'][0],
            text='Initial text', draft=draft), uuid4())
        document = repo.document(identity)
        second_body = personal(document, parent=first['id'], text='Further text', draft=draft)
        second_operation = uuid4()
        second = repo.write_personal(identity, second_body, second_operation)
        assert first['id'] != second['id']
        assert second['parent_revision_id'] == first['id']
        assert repo.document(identity)['latest_personal_revision_id'] == second['id']
        assert repo.revision(UUID(first['id']))['body_markdown'] == 'Initial text'
        conflict = repo.write_personal(identity, dict(second_body, body_markdown='Other device same draft'), uuid4())
        assert conflict['submitted_revision']['body_markdown'] == 'Other device same draft'
        assert repo.document(identity)['latest_personal_revision_id'] == second['id']
    with pytest.raises(IdempotencyMismatch), pg.db.transaction(owners.a) as tx:
        PgReviewRepository(tx).write_personal(identity, dict(second_body, body_markdown='Changed operation'), second_operation)


def test_concurrent_personal_edits_preserve_both_drafts(pg, owners, tree):
    _, _, _, result = generate(pg, owners, tree)
    identity = UUID(result['summary_document_id'])
    with pg.db.transaction(owners.a) as tx:
        document = PgReviewRepository(tx).document(identity)

    def save(index):
        with pg.db.transaction(owners.a) as tx:
            return PgReviewRepository(tx).write_personal(identity,
                personal(document, parent=document['candidate_revision_ids'][0], text=f'Device {index}'), uuid4())

    with ThreadPoolExecutor(max_workers=2) as workers:
        values = list(workers.map(save, range(2)))
    assert sum('conflict_id' in value for value in values) == 1
    with pg.db.transaction(owners.a) as tx:
        versions = PgReviewRepository(tx).revisions(identity)['items']
        assert {row['body_markdown'] for row in versions if row['kind']=='personal'} == {'Device 0', 'Device 1'}


def test_duplicate_publication_is_serialized_and_transactional(pg, owners, tree):
    seeded(pg, owners.a, tree)
    plan, task = prepare_review(pg, owners.a, tree)

    def publish():
        with pg.db.transaction(owners.a) as tx:
            return PgReviewRepository(tx).publish(task, output(), UUID(plan['source_snapshot']['id']), REQUEST)

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: publish(), range(2)))
    assert set(results[0]['revision_ids']) == set(results[1]['revision_ids'])
    with pg.db.transaction(owners.a) as tx:
        assert len(PgReviewRepository(tx).documents(class_session_id=tree.classroom.id)['items']) == 2


def test_revoked_snapshot_hides_entire_generated_family_and_personal_versions(pg, owners, tree):
    source, _, _, result = generate(pg, owners, tree)
    identity = UUID(result['summary_document_id'])
    with pg.db.transaction(owners.a) as tx:
        repo = PgReviewRepository(tx)
        document = repo.document(identity)
        saved = repo.write_personal(identity, personal(document, parent=document['candidate_revision_ids'][0]), uuid4())
        PgQaRepository(tx).revoke_source(source.source_id)
    with pg.db.transaction(owners.a) as tx:
        for table in REVIEW_TABLES:
            assert tx.connection.scalar(sa.text(f'SELECT count(*) FROM {table} WHERE owner_id=:owner'),
                                        {'owner': owners.a.owner_id}) == 0
    with pytest.raises(QaRejected), pg.db.transaction(owners.a) as tx:
        PgReviewRepository(tx).revision(UUID(saved['id']))
    with pytest.raises(QaRejected), pg.db.transaction(owners.a) as tx:
        PgReviewRepository(tx).document(identity)


def test_source_revision_does_not_drift_history(pg, owners, tree):
    source, plan, _, result = generate(pg, owners, tree)
    identity = UUID(result['summary_document_id'])
    with pg.db.transaction(owners.a) as tx:
        PgQaRepository(tx).revise_source(source.source_id, 'New synthetic source revision with different wording.')
        repo = PgReviewRepository(tx)
        document = repo.document(identity)
        assert document['source_status'] == 'changed'
        revision = repo.revision(UUID(document['candidate_revision_ids'][0]))
        assert revision['structured_content'] == output()
        snapshot = PgQaRepository(tx).snapshot(UUID(revision['source_snapshot_id']))
        assert snapshot == plan['source_snapshot']


def test_revoked_later_personal_source_does_not_freeze_visible_document(pg, owners, tree):
    first, _, _, generated = generate(pg, owners, tree)
    identity = UUID(generated['summary_document_id'])
    with pg.db.transaction(owners.a) as tx:
        repository = PgReviewRepository(tx)
        original = repository.document(identity)
        first_candidate = original['candidate_revision_ids'][0]
        second = PgQaRepository(tx).add_source(tree.classroom.id, kind='transcript_segment',
            object_id=uuid4(), text='A second independently revocable classroom source.',
            start_ms=5000, end_ms=7000)
    request = dict(REQUEST, target_document_id=str(identity), base_revision=original['revision'])
    plan, task = prepare_review(pg, owners.a, tree, request=request)
    with pg.db.transaction(owners.a) as tx:
        repository = PgReviewRepository(tx)
        updated = repository.publish(task, output(), UUID(plan['source_snapshot']['id']), request)
        document = repository.document(identity)
        personal_revision = repository.write_personal(identity,
            personal(document, parent=updated['revision_ids'][0]), uuid4())
        PgQaRepository(tx).revoke_source(second.source_id)
    with pytest.raises(QaRejected), pg.db.transaction(owners.a) as tx:
        PgReviewRepository(tx).revision(UUID(personal_revision['id']))
    with pg.db.transaction(owners.a) as tx:
        repository = PgReviewRepository(tx)
        document = repository.document(identity)
        assert document['latest_personal_revision_id'] == personal_revision['id']
        assert document['source_status'] == 'unavailable'
        renamed = repository.update_document(identity,
            dict(base_revision=document['revision'], title='User chosen title', understood=True), uuid4())
        adopted = repository.adopt(identity,
            dict(base_revision=renamed['revision'], revision_id=first_candidate), uuid4())
        assert adopted['adopted_revision_id'] == first_candidate
        assert adopted['latest_personal_revision_id'] == personal_revision['id']
    request['base_revision'] = adopted['revision']
    request['seen_sources'] = [dict(kind=first.kind, id=str(first.source_id),
        revision_id=str(first.revision_id), content_sha256=first.content_sha256)]
    plan, task = prepare_review(pg, owners.a, tree, request=request)
    with pg.db.transaction(owners.a) as tx:
        repository = PgReviewRepository(tx)
        repository.publish(task, output(), UUID(plan['source_snapshot']['id']), request)
        assert repository.document(identity)['adopted_revision_id'] == first_candidate


def test_cross_owner_and_cross_document_access_fail_closed(pg, owners, tree):
    _, _, _, result = generate(pg, owners, tree)
    identity = UUID(result['summary_document_id'])
    with pg.db.transaction(owners.b) as tx:
        for table in REVIEW_TABLES:
            assert tx.connection.scalar(sa.text(f'SELECT count(*) FROM {table}')) == 0
    with pytest.raises(QaRejected), pg.db.transaction(owners.b) as tx:
        PgReviewRepository(tx).document(identity)
    with pg.db.transaction(owners.a) as tx:
        repo = PgReviewRepository(tx)
        summary = repo.document(identity)
        card = repo.document(UUID(result['document_ids'][1]))
    with pytest.raises(QaRejected), pg.db.transaction(owners.a) as tx:
        PgReviewRepository(tx).adopt(identity, dict(base_revision=summary['revision'],
                                                  revision_id=card['candidate_revision_ids'][0]), uuid4())


def test_direct_sql_cross_document_pointer_and_mutable_revision_rejected(pg, owners, tree):
    _, _, _, result = generate(pg, owners, tree)
    with pg.db.transaction(owners.a) as tx:
        repo = PgReviewRepository(tx)
        summary = repo.document(UUID(result['summary_document_id']))
        card = repo.document(UUID(result['document_ids'][1]))
    with pytest.raises(sa.exc.DBAPIError) as error, pg.db.transaction(owners.a) as tx:
        tx.connection.execute(sa.text('''UPDATE review_documents SET adopted_revision_id=:candidate,
            revision=revision+1 WHERE id=:document'''),
            {'candidate': UUID(card['candidate_revision_ids'][0]), 'document': UUID(summary['id'])})
    assert error.value.orig.sqlstate == '23503'
    with pytest.raises(sa.exc.DBAPIError) as immutable, pg.admin.begin() as connection:
        connection.execute(sa.text("UPDATE review_revisions SET body_markdown='overwrite' WHERE id=:id"),
                           {'id': UUID(summary['candidate_revision_ids'][0])})
    assert immutable.value.orig.sqlstate == '23514'


def test_personal_note_without_source_and_explicit_understanding(pg, owners, tree):
    body = dict(class_session_id=str(tree.classroom.id), title='Personal', body_markdown='My own notes', draft_id=str(uuid4()))
    operation = uuid4()
    with pg.db.transaction(owners.a) as tx:
        repo = PgReviewRepository(tx)
        document = repo.create_note(body, operation)
        assert document == repo.create_note(body, operation)
        revision = repo.revision(UUID(document['latest_personal_revision_id']))
        assert revision['source_snapshot_id'] is None
        updated = repo.update_document(UUID(document['id']), dict(base_revision=document['revision'], understood=True), uuid4())
        assert updated['understood'] is True
        assert repo.document(UUID(document['id']), revision=document['revision']) == document
        assert len(repo.revisions(UUID(document['id']))['items']) == 1


def test_classroom_tombstone_revokes_personal_note(pg, owners, tree):
    with pg.db.transaction(owners.a) as tx:
        document = PgReviewRepository(tx).create_note(dict(class_session_id=str(tree.classroom.id),
            title='Personal', body_markdown='My own notes', draft_id=str(uuid4())), uuid4())
        tx.tombstones.revoke(Resource.CLASS_SESSION, tree.classroom.id, tree.classroom.revision, 1)
    with pytest.raises(QaRejected), pg.db.transaction(owners.a) as tx:
        PgReviewRepository(tx).document(UUID(document['id']))


def test_signed_pagination_binds_owner_and_all_filters(pg, owners, tree):
    previous = pg.db.cursor_secret
    pg.db.cursor_secret = b'pr07b-review-pages-secret-32-bytes!'
    try:
        for number in range(3):
            with pg.db.transaction(owners.a) as tx:
                PgReviewRepository(tx).create_note(dict(class_session_id=str(tree.classroom.id), title=f'Note {number}',
                    body_markdown='Personal', draft_id=str(uuid4())), uuid4())
        filters = dict(semester_id=tree.semester.id, course_id=tree.course.id,
                       class_session_id=tree.classroom.id, kind='note', understood=False, limit=1)
        with pg.db.transaction(owners.a) as tx:
            repo = PgReviewRepository(tx)
            first = repo.documents(**filters)
            assert len(first['next_cursor']) <= 512
            second = repo.documents(**filters, cursor=first['next_cursor'])
            third = repo.documents(**filters, cursor=second['next_cursor'])
            assert len({page['items'][0]['id'] for page in (first, second, third)}) == 3
            assert third['next_cursor'] is None
        with pytest.raises(QaRejected), pg.db.transaction(owners.a) as tx:
            PgReviewRepository(tx).documents(**dict(filters, understood=True), cursor=first['next_cursor'])
        with pytest.raises(QaRejected), pg.db.transaction(owners.a) as tx:
            PgReviewRepository(tx).documents(**filters, cursor=first['next_cursor']+'x')
    finally:
        pg.db.cursor_secret = previous


def test_partial_publication_failure_rolls_back_every_artifact(pg, owners, tree, monkeypatch):
    seeded(pg, owners.a, tree)
    plan, task = prepare_review(pg, owners.a, tree)
    original = PgReviewRepository._append

    def fail_card(self, document, **kwargs):
        if document['kind']=='card':
            raise RuntimeError('synthetic database write failure')
        return original(self, document, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(PgReviewRepository, '_append', fail_card)
        with pytest.raises(RuntimeError), pg.db.transaction(owners.a) as tx:
            PgReviewRepository(tx).publish(task, output(), UUID(plan['source_snapshot']['id']), REQUEST)
    with pg.db.transaction(owners.a) as tx:
        repo = PgReviewRepository(tx)
        assert repo.documents(class_session_id=tree.classroom.id)['items'] == []
        saved = repo.publish(task, output(), UUID(plan['source_snapshot']['id']), REQUEST)
        assert len(saved['document_ids']) == 2


def test_upgrade_after_pr07a_is_single_chain_and_preserves_existing_rows(previous_schema):
    database = previous_schema
    upgrade(database, 'pr07a_001')
    before = database_snapshot(database, catalog_tables | task_tables)
    tables_before = set(sa.inspect(database.engine).get_table_names())
    upgrade(database, 'pr07b_001')
    upgrade(database, 'pr07b_001')
    assert version(database) == 'pr07b_001'
    assert database_snapshot(database, catalog_tables | task_tables) == before
    assert set(sa.inspect(database.engine).get_table_names()) == tables_before | REVIEW_TABLES
