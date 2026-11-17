"""PR-04b deletion lifecycle on real PostgreSQL, two real S3-compatible stores and the real worker."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa

from deletion_support import *  # noqa: F401,F403  (fixtures and helpers)
from deletion_support import POLICY, StallProxy, build, ledger_store, object_store
from test_private_media import CONTENT, access, available, create, headers, put
from classroom_web.application.deletion import DeletionWorker
from classroom_web.domain.auth import AuthRejected
from classroom_web.domain.data import Gone, InactiveOwner, OwnerContext
from classroom_web.domain.deletion import (DeletionIntent, DeletionPolicy, LedgerCorrupt, LedgerUnavailable,
                                           SettleWindowOpen, StaleDeletionFence)
from classroom_web.infrastructure.deletion_repository import PgDeletionQueue, PgDeletionRepository

PNG = b'\x89PNG\r\n\x1a\n'


def content(marker):
    return PNG + f'SYNTHETIC-{marker}-{uuid4().hex}'.encode() * 50


def preview(m, kind, target):
    path = {'class_session': f'/class-sessions/{target}/deletion-preview', 'audio': f'/class-sessions/{target}/audio-deletion-preview',
            'course': f'/courses/{target}/deletion-preview', 'semester': f'/semesters/{target}/deletion-preview'}[kind]
    return m.client.get(BASE + path)


def submit(m, kind, target, shown, key=None, **changes):
    body = dict(confirmation_token=shown['confirmation_token'], base_revision=shown['revision'])
    if kind in ('course', 'semester'):
        body['mode'] = 'cascade'
    body.update(changes)
    path = {'class_session': f'/class-sessions/{target}/delete', 'audio': f'/class-sessions/{target}/audio/delete',
            'course': f'/courses/{target}/delete', 'semester': f'/semesters/{target}/delete'}[kind]
    return m.client.post(BASE + path, json=body, headers=headers(m, key))


def delete(m, kind, target):
    shown = preview(m, kind, target)
    assert shown.status_code == 200, shown.text
    reply = submit(m, kind, target, shown.json())
    assert reply.status_code == 202, (reply.text, list(m.client.app.state.errors))
    return reply.json()


def status(m, deletion_id):
    reply = m.client.get(BASE + f'/deletions/{deletion_id}')
    assert reply.status_code == 200, reply.text
    return reply.json()


def finished(m, deletion_id):
    """Objects are gone and the request honestly stays open (see ``settled``)."""
    shown = status(m, deletion_id)
    return settled(m, deletion_id) and shown['state'] == 'pending_delete' and shown['physical_completed_at'] is None


def events(m, deletion_id):
    return [row['payload']['state'] for row in rows(m, """SELECT payload FROM outbox_events
        WHERE event_type='object.deleted' AND payload->>'deletion_id'=:id ORDER BY event_sequence""", id=str(deletion_id))]


def upload(m, room=None, marker='asset'):
    """A committed asset in the given class session; returns (asset DTO, bytes)."""
    original, data = m.classroom, content(marker)
    m.classroom = room or original
    try:
        staged, body = create(m, data)
        assert put(m, staged, data).status_code == 200
        done = m.client.post(BASE + f'/media/uploads/{staged["id"]}/complete',
                             json={k: body[k] for k in ('sha256', 'byte_length')}, headers=headers(m))
        assert done.status_code == 200, done.text
        return done.json(), data
    finally:
        m.classroom = original


def asset_state(m, asset_id):
    return one(m, 'SELECT status,access_revoked_at,deleted_at FROM media_assets WHERE id=:id', id=asset_id)


def test_schema_matches_frozen_inventory_and_roles_are_narrow(d):
    m = d
    model = json.loads((Path(__file__).resolve().parents[4] / 'docs/web-alpha/architecture/contracts/domain-model.json').read_text(encoding='utf8'))
    frozen = next(table for table in model['tables'] if table['name'] == 'deletion_assets')
    inspector = sa.inspect(m.pg.admin)
    actual = {column['name']: column for column in inspector.get_columns('deletion_assets')}
    assert set(actual) == {column['name'] for column in frozen['columns']}
    for column in frozen['columns']:
        assert actual[column['name']]['nullable'] == column['nullable']
    foreign = inspector.get_foreign_keys('deletion_assets')
    for expected in frozen['foreign_keys']:
        assert any(item['constrained_columns'] == expected['columns'] and item['referred_table'] == expected['references']['table']
                   and item['options']['ondelete'] == expected['on_delete'] for item in foreign)
    # The frozen request/tombstone tables keep exactly their frozen columns.
    for name in ('deletion_requests', 'deletion_tombstones'):
        table = next(table for table in model['tables'] if table['name'] == name)
        assert {c['name'] for c in inspector.get_columns(name)} == {c['name'] for c in table['columns']}
    secured = rows(m, """SELECT relname FROM pg_class WHERE relname IN ('deletion_assets','deletion_executions')
        AND relrowsecurity AND relforcerowsecurity""")
    assert len(secured) == 2
    roles = rows(m, """SELECT rolname,rolcanlogin,rolsuper,rolbypassrls,rolinherit FROM pg_roles
        WHERE rolname IN ('classroom_web_eraser','classroom_web_deletion_worker','classroom_web_restore_operator')""")
    assert len(roles) == 3 and not any(r['rolcanlogin'] or r['rolsuper'] or r['rolbypassrls'] for r in roles)
    assert rows(m, """SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid
        WHERE r.rolname='classroom_web_eraser'""") == []
    asset, _ = upload(m)
    done = delete(m, 'class_session', m.classroom.id)
    # The runtime may read its own deletion state but never write it directly or act as the worker.
    for statement in ("UPDATE deletion_assets SET state='absent',confirmed_absent_at=clock_timestamp()",
                      'UPDATE deletion_executions SET ledger_generation=999999',
                      "INSERT INTO deletion_executions(request_id,owner_id,intent_id,origin,settle_until,not_before) VALUES(gen_random_uuid(),gen_random_uuid(),gen_random_uuid(),'user',now(),now())",
                      "SELECT * FROM classroom_web_claim_deletion('intruder',30)",
                      'SELECT classroom_web_restore_seal()', 'SELECT classroom_web_restore_block(ARRAY[]::uuid[])',
                      "SELECT classroom_web_revoke_media_impl('{}'::jsonb)",
                      'DELETE FROM deletion_requests', "INSERT INTO retention_policies(version,media_kind,max_lifetime,approval_reference,synthetic) VALUES('fake-policy','slide','1 day','none',true)"):
        with pytest.raises(sa.exc.DBAPIError):
            with m.pg.db.transaction(OwnerContext(m.uid)) as tx:
                tx.connection.execute(sa.text(statement))
    # The worker identity sees no private table and cannot use owner or restore functions.
    with m.deploy.queue.engine.connect() as c:
        for statement in ('SELECT * FROM media_assets', 'SELECT * FROM users', 'SELECT * FROM deletion_requests',
                          'SELECT * FROM class_sessions', "SELECT classroom_web_owner_revoke_media('{}'::jsonb)",
                          'SELECT classroom_web_restore_seal()', 'SELECT classroom_web_restore_block(ARRAY[]::uuid[])',
                          'SELECT classroom_web_seal_account(gen_random_uuid())'):
            with pytest.raises(sa.exc.DBAPIError):
                c.execute(sa.text(statement))
            c.rollback()
    # A runtime or migration login is refused as a deletion worker.
    for url in (m.pg.runtime_url, m.pg.admin_url):
        wrong = PgDeletionQueue(url.render_as_string(hide_password=False))
        try:
            with pytest.raises(PermissionError):
                wrong.claim('wrong-identity', 30)
        finally:
            wrong.dispose()
    assert asset_state(m, asset['id'])['status'] == 'delete_pending' and done['state'] == 'pending_delete'
    m.worker.drain()


def test_class_session_delete_revokes_first_then_erases_every_version(d):
    m = d
    asset, data = upload(m, marker='committed')
    path = access(m, asset)
    assert m.client.get(path).content == data
    # A staged upload whose object was written but never confirmed, and one never written.
    staged_data = content('staged')
    staged, _ = create(m, staged_data)
    assert put(m, staged, staged_data).status_code == 200
    empty, _ = create(m, content('empty'))
    keys = [object_key(m, asset['id']), object_key(m, staged['asset_id']), object_key(m, empty['asset_id'])]
    # Older history of the committed object: a second stored version of the same key.
    m.store.client.put_object(Bucket=m.store.bucket, Key=keys[0], Body=b'older-synthetic-version')
    assert len(versions(m, keys[0])) == 2
    shown = preview(m, 'class_session', m.classroom.id).json()
    assert shown['class_session_count'] == 1 and shown['media_bytes'] == len(data) + len(staged_data) + len(content('empty'))
    key = str(uuid4())
    accepted = submit(m, 'class_session', m.classroom.id, shown, key)
    assert accepted.status_code == 202, accepted.text
    deletion = accepted.json()
    assert deletion['state'] == 'pending_delete' and deletion['scope_type'] == 'class_session'
    assert deletion['physical_completed_at'] is None and deletion['failure_code'] is None
    # Access is gone before any byte is physically removed.
    assert m.client.get(path).status_code == 410
    assert m.client.post(BASE + f'/media/{asset["id"]}/access', json={}, headers=headers(m)).status_code == 410
    assert put(m, staged, staged_data).status_code == 410
    assert m.client.post(BASE + '/media/uploads', json=dict(purpose='slide', class_session_id=str(m.classroom.id),
        mime_type='image/png', byte_length=len(data), sha256='0' * 64, filename='late.png'), headers=headers(m)).status_code == 410
    with pytest.raises(Gone):
        with m.pg.db.transaction(OwnerContext(m.uid)) as tx:
            tx.catalog.get_class_session(m.classroom.id)
    assert len(versions(m, keys[0])) == 2 and m.store.inspect(keys[1]) is not None
    assert status(m, deletion['id'])['state'] == 'pending_delete'
    assert events(m, deletion['id']) == ['pending_delete']
    execution = one(m, 'SELECT * FROM deletion_executions WHERE request_id=:id', id=deletion['id'])
    entry = next(e for e in m.deploy.ledger.entries() if e.generation == execution['ledger_generation'])
    assert entry.intent == DeletionIntent.for_scope(m.uid, 'class_session', m.classroom.id)
    inventory = rows(m, 'SELECT object_key,state FROM deletion_assets WHERE request_id=:id', id=deletion['id'])
    assert sorted(item['object_key'] for item in inventory) == sorted(keys)
    # The same request repeated is the same receipt, with no second request or ledger entry.
    again = submit(m, 'class_session', m.classroom.id, shown, key)
    assert again.status_code == 202 and again.json()['id'] == deletion['id']
    other_key = submit(m, 'class_session', m.classroom.id, shown)
    assert other_key.status_code == 202 and other_key.json()['id'] == deletion['id']
    assert submit(m, 'class_session', m.classroom.id, shown, key, base_revision=shown['revision'] + 1).status_code == 409
    assert preview(m, 'class_session', m.classroom.id).status_code == 410
    assert m.worker.drain() >= 1
    final = status(m, deletion['id'])
    # Not reported as deleted: the classroom's database content is outside this package.
    assert finished(m, deletion['id']) and final['revision'] > deletion['revision']
    assert m.deploy.queue.audit()['content_erasure_outstanding'] >= 1
    assert m.worker.run_once() is False
    for stored in keys:
        assert versions(m, stored) == [] and m.store.inspect(stored) is None
    assert all(asset_state(m, i)['status'] == 'deleted' and asset_state(m, i)['deleted_at']
               for i in (asset['id'], staged['asset_id'], empty['asset_id']))
    assert all(item['state'] == 'absent' and item['confirmed_absent_at'] for item in
               rows(m, 'SELECT state,confirmed_absent_at FROM deletion_assets WHERE request_id=:id', id=deletion['id']))
    assert events(m, deletion['id']) == ['pending_delete']
    assert len(rows(m, "SELECT 1 FROM deletion_requests WHERE owner_id=:o AND scope_id=:s", o=m.uid, s=m.classroom.id)) == 1
    assert submit(m, 'class_session', m.classroom.id, shown, key).json()['state'] == 'pending_delete'
    assert m.client.get(path).status_code == 410
    assert 'ticket=' not in json.dumps(list(m.client.app.state.errors), default=str)


def test_confirmation_is_bound_to_version_extent_session_and_time(d):
    m = d
    upload(m)
    shown = preview(m, 'class_session', m.classroom.id).json()
    other_room = classroom(m, 'Second synthetic classroom')
    other = preview(m, 'class_session', other_room.id).json()
    assert preview(m, 'class_session', str(uuid4())).status_code == 404
    assert m.client.get(BASE + f'/class-sessions/{m.classroom.id}/deletion-preview?x=1').status_code == 422
    assert m.client.post(BASE + f'/class-sessions/{m.classroom.id}/delete', json=dict(
        confirmation_token=shown['confirmation_token'], base_revision=shown['revision'])).status_code == 403
    assert submit(m, 'class_session', m.classroom.id, shown, base_revision=shown['revision'] + 5).status_code == 409
    assert submit(m, 'class_session', m.classroom.id, shown, confirmation_token=other['confirmation_token']).status_code == 422
    expires, signature = shown['confirmation_token'].split('.')
    assert submit(m, 'class_session', m.classroom.id, shown, confirmation_token=f'{int(expires) + 600}.{signature}').status_code == 422
    assert submit(m, 'class_session', m.classroom.id, shown, confirmation_token='not-a-token').status_code == 422
    # The extent changed after the user saw the preview.
    upload(m, marker='added-after-preview')
    assert submit(m, 'class_session', m.classroom.id, shown).status_code == 422
    fresh = preview(m, 'class_session', m.classroom.id).json()
    key = str(uuid4())
    real = m.deletion.clock
    m.deletion.clock = SimpleNamespace(now=lambda: datetime.now(timezone.utc) + timedelta(minutes=6))
    try:
        assert submit(m, 'class_session', m.classroom.id, fresh, key).status_code == 422
    finally:
        m.deletion.clock = real
    # None of the refused requests changed anything.
    assert rows(m, 'SELECT 1 FROM deletion_requests WHERE owner_id=:o', o=m.uid) == []
    assert submit(m, 'class_session', m.classroom.id, fresh, key).status_code == 202
    # Same key, different body.
    assert submit(m, 'class_session', m.classroom.id, fresh, key, base_revision=fresh['revision'] + 1).status_code == 409
    # Moving children instead of deleting them is not implemented and is refused, not guessed.
    course = preview(m, 'course', m.course.id).json()
    assert submit(m, 'course', m.course.id, course, mode='move_children', destination_id=str(uuid4())).status_code == 503
    assert submit(m, 'course', m.course.id, course, destination_id=str(uuid4())).status_code == 422
    with m.pg.db.transaction(OwnerContext(m.uid)) as tx:
        assert tx.catalog.get_course(m.course.id).deleted_at is None
    m.worker.drain()


def test_concurrent_and_repeated_deletes_of_one_object_are_one_request(d):
    m = d
    asset, _ = upload(m)
    shown = preview(m, 'class_session', m.classroom.id).json()
    with ThreadPoolExecutor(6) as pool:
        replies = list(pool.map(lambda _: submit(m, 'class_session', m.classroom.id, shown), range(6)))
    assert [r.status_code for r in replies] == [202] * 6, [r.text for r in replies]
    assert len({r.json()['id'] for r in replies}) == 1
    deletion_id = replies[0].json()['id']
    assert len(rows(m, 'SELECT 1 FROM deletion_requests WHERE owner_id=:o', o=m.uid)) == 1
    assert len(rows(m, 'SELECT 1 FROM deletion_tombstones WHERE owner_id=:o', o=m.uid)) == 1
    intent = DeletionIntent.for_scope(m.uid, 'class_session', m.classroom.id)
    assert len([e for e in m.deploy.ledger.entries() if e.intent.intent_id == intent.intent_id]) == 1
    assert events(m, deletion_id) == ['pending_delete']
    # Several workers race for the same request; exactly one physical pass completes it.
    workers = [DeletionWorker(queue=m.deploy.queue, store=m.store, policy=POLICY, worker_id=f'racer-{i}') for i in range(4)]
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda w: w.drain(), workers))
    assert finished(m, deletion_id)
    assert events(m, deletion_id) == ['pending_delete']
    assert versions(m, object_key(m, asset['id'])) == []


def test_course_and_semester_cascades_cover_every_descendant(d):
    m = d
    with m.pg.db.transaction(OwnerContext(m.uid)) as tx:
        kept_course = tx.catalog.create_course(m.semester.id, 'Kept synthetic course')
        other_semester = tx.catalog.create_semester('Untouched synthetic semester')
        far_course = tx.catalog.create_course(other_semester.id, 'Far synthetic course')
    second = classroom(m, 'Second synthetic classroom')
    kept_room, far_room = classroom(m, 'Kept', kept_course), classroom(m, 'Far', far_course)
    first_asset, _ = upload(m)
    second_asset, _ = upload(m, second)
    kept_asset, kept_data = upload(m, kept_room)
    far_asset, far_data = upload(m, far_room)
    shown = preview(m, 'course', m.course.id).json()
    assert shown['class_session_count'] == 2
    done = delete(m, 'course', m.course.id)
    for room in (m.classroom, second):
        with pytest.raises(Gone):
            with m.pg.db.transaction(OwnerContext(m.uid)) as tx:
                tx.catalog.get_class_session(room.id)
    assert all(asset_state(m, a['id'])['status'] == 'delete_pending' for a in (first_asset, second_asset))
    # A descendant cannot be moved out from under the revoked course.
    with pytest.raises((Gone, sa.exc.DBAPIError)):
        with m.pg.db.transaction(OwnerContext(m.uid)) as tx:
            tx.catalog.move_class_session(second.id, second.revision, kept_course.id)
    assert m.client.get(access(m, kept_asset)).content == kept_data
    m.worker.drain()
    assert finished(m, done['id'])
    assert asset_state(m, kept_asset['id'])['status'] == 'committed'
    whole = delete(m, 'semester', m.semester.id)
    assert asset_state(m, kept_asset['id'])['status'] == 'delete_pending'
    assert m.client.post(BASE + f'/media/{kept_asset["id"]}/access', json={}, headers=headers(m)).status_code == 410
    m.worker.drain()
    assert finished(m, whole['id'])
    assert versions(m, object_key(m, kept_asset['id'])) == []
    # A sibling semester of the same owner is untouched.
    assert m.client.get(access(m, far_asset)).content == far_data
    assert asset_state(m, far_asset['id'])['status'] == 'committed'


def test_other_owner_cannot_see_request_or_affect_deletion(d):
    m = d
    mine, mine_data = upload(m)
    owner_email, owner_uid = m.a.email, m.uid
    other = new_user(m)
    as_user(m, other.email)
    with m.pg.db.transaction(OwnerContext(other.uid)) as tx:
        semester = tx.catalog.create_semester('Other synthetic owner')
        course = tx.catalog.create_course(semester.id, 'Other course')
        room = tx.catalog.create_class_session(course.id, 'Other classroom')
    theirs, their_data = upload(m, room)
    # B gets the same answer for A's objects as for objects that do not exist.
    assert preview(m, 'class_session', m.classroom.id).status_code == 404
    assert preview(m, 'course', m.course.id).status_code == 404
    forged = preview(m, 'class_session', room.id).json()
    assert submit(m, 'class_session', m.classroom.id, forged).status_code == 404
    as_user(m, owner_email)
    done = delete(m, 'class_session', m.classroom.id)
    as_user(m, other.email)
    assert m.client.get(BASE + f'/deletions/{done["id"]}').status_code == 404
    # FORCE RLS: B's own transaction sees none of A's deletion rows, and cannot apply an intent for A.
    with m.pg.db.transaction(OwnerContext(other.uid)) as tx:
        for table in ('deletion_requests', 'deletion_executions', 'deletion_assets', 'deletion_tombstones'):
            assert tx.connection.scalar(sa.text(f'SELECT count(*) FROM {table}')) == 0
        with pytest.raises(ValueError):
            PgDeletionRepository(tx, other.uid).apply(DeletionIntent.for_scope(owner_uid, 'media_asset', uuid4()), None, 0)
    with pytest.raises(sa.exc.DBAPIError):
        with m.pg.db.transaction(OwnerContext(other.uid)) as tx:
            intent = DeletionIntent.for_scope(owner_uid, 'media_asset', uuid4())
            tx.connection.execute(sa.text('SELECT classroom_web_owner_revoke_media(CAST(:p AS jsonb))'),
                                  dict(p=json.dumps(dict(intent.record(), generation=None, settle_seconds=0))))
    m.worker.drain()
    # A's deletion and the worker left B's data alone.
    assert m.client.get(access(m, theirs)).content == their_data
    assert asset_state(m, theirs['id'])['status'] == 'committed'
    assert versions(m, object_key(m, mine['id'])) == []
    as_user(m, owner_email)
    assert finished(m, done['id'])


def test_ledger_outage_denies_access_locally_and_is_not_an_accepted_deletion(d):
    m = d
    first_asset, _ = upload(m)
    second_room = classroom(m, 'Second synthetic classroom')
    second_asset, _ = upload(m, second_room)
    first, second = preview(m, 'class_session', m.classroom.id).json(), preview(m, 'class_session', second_room.id).json()
    key = str(uuid4())
    m.deploy.services.ledger.stop()
    try:
        refused = submit(m, 'class_session', m.classroom.id, first, key)
        assert refused.status_code == 503, refused.text
        assert submit(m, 'class_session', second_room.id, second).status_code == 503
        request = one(m, "SELECT d.id,d.state,x.ledger_generation FROM deletion_requests d JOIN deletion_executions x ON x.request_id=d.id WHERE d.owner_id=:o AND d.scope_id=:s", o=m.uid, s=m.classroom.id)
        # Not accepted: no ledger position, state says only that access is revoked.
        assert request['state'] == 'access_revoked' and request['ledger_generation'] is None
        assert status(m, request['id'])['state'] == 'access_revoked'
        assert events(m, request['id']) == ['access_revoked']
        # Access is nevertheless denied now, and nothing is physically removed without acceptance.
        assert m.client.post(BASE + f'/media/{first_asset["id"]}/access', json={}, headers=headers(m)).status_code == 410
        m.worker.drain()
        assert m.store.inspect(object_key(m, first_asset['id'])) is not None
        assert one(m, 'SELECT state FROM deletion_requests WHERE id=:id', id=request['id'])['state'] == 'access_revoked'
        assert m.deploy.queue.audit()['unaccepted_requests'] >= 2
        with pytest.raises(LedgerUnavailable):
            m.deletion.reconcile()
        assert m.deletion.ready is False
        assert m.client.get(BASE + '/auth/session').status_code == 503
    finally:
        m.deploy.services.ledger.start()
    # reconcile() completes the acceptance of the request nobody retried.
    report = m.deletion.reconcile()
    assert len(report['accepted']) == 2 and report['unresolved'] == [] and m.deletion.ready
    assert m.deploy.queue.audit()['unaccepted_requests'] == 0
    # The client's retry of the same key now receives the accepted request.
    retried = submit(m, 'class_session', m.classroom.id, first, key)
    assert retried.status_code == 202 and retried.json()['id'] == str(request['id'])
    assert retried.json()['state'] == 'pending_delete'
    assert events(m, request['id']) == ['access_revoked', 'pending_delete']
    m.worker.drain()
    assert finished(m, request['id'])
    assert versions(m, object_key(m, first_asset['id'])) == [] and versions(m, object_key(m, second_asset['id'])) == []


def test_crash_after_ledger_append_is_replayed_into_the_database(d, monkeypatch):
    m = d
    asset, data = upload(m)
    path = access(m, asset)
    shown = preview(m, 'class_session', m.classroom.id).json()
    key = str(uuid4())
    original = PgDeletionRepository.apply
    monkeypatch.setattr(PgDeletionRepository, 'apply', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('synthetic crash before commit')))
    lost = submit(m, 'class_session', m.classroom.id, shown, key)
    assert lost.status_code == 503
    monkeypatch.setattr(PgDeletionRepository, 'apply', original)
    intent = DeletionIntent.for_scope(m.uid, 'class_session', m.classroom.id)
    # The ledger has the intent; the database has nothing yet and never answered 202.
    assert [e for e in m.deploy.ledger.entries() if e.intent == intent]
    assert rows(m, 'SELECT 1 FROM deletion_requests WHERE owner_id=:o', o=m.uid) == []
    assert m.client.get(path).content == data
    # A restarted process reconciles before serving.
    restarted = build(m.a, m.deploy, m.spool, ledger=ledger_store(m.deploy.services, m.deploy.ledger_bucket), reconcile=False)
    assert restarted.deletion.ready is False
    report = restarted.deletion.reconcile()
    assert len(report['applied']) == 1 and report['unresolved'] == [] and restarted.deletion.ready
    assert m.client.get(path).status_code == 410
    request = one(m, 'SELECT id,state FROM deletion_requests WHERE owner_id=:o', o=m.uid)
    assert request['state'] == 'pending'
    assert restarted.deletion.reconcile()['applied'] == []
    retried = submit(m, 'class_session', m.classroom.id, shown, key)
    assert retried.status_code == 202 and retried.json()['id'] == str(request['id'])
    m.worker.drain()
    assert finished(m, request['id'])


def test_storage_outage_fails_closed_keeps_access_revoked_and_retries(d):
    m = d
    m.worker.drain()
    first, _ = upload(m, marker='one')
    second, _ = upload(m, marker='two')
    done = delete(m, 'class_session', m.classroom.id)
    m.deploy.services.media.stop()
    try:
        assert m.worker.run_once() is True
        failed = status(m, done['id'])
        assert failed['state'] == 'delete_failed' and failed['failure_code'] == 'STORAGE_UNAVAILABLE'
        assert failed['physical_completed_at'] is None
        assert asset_state(m, first['id'])['status'] == 'delete_failed'
        assert events(m, done['id']) == ['pending_delete', 'delete_failed']
        inventory = rows(m, 'SELECT state,attempt_count,last_error_code FROM deletion_assets WHERE request_id=:id', id=done['id'])
        assert all(i['state'] == 'failed' and i['attempt_count'] == 1 and i['last_error_code'] == 'STORAGE_UNAVAILABLE' for i in inventory)
        # Still unreadable, and a second failed attempt neither completes nor repeats the event.
        assert m.client.post(BASE + f'/media/{first["id"]}/access', json={}, headers=headers(m)).status_code == 410
        assert m.worker.run_once() is True
        assert status(m, done['id'])['state'] == 'delete_failed'
        assert events(m, done['id']) == ['pending_delete', 'delete_failed']
        assert m.deploy.queue.audit()['failed_requests'] >= 1
    finally:
        m.deploy.services.media.start()
    m.worker.drain()
    final = status(m, done['id'])
    assert finished(m, done['id']) and final['failure_code'] is None
    assert versions(m, object_key(m, first['id'])) == [] and versions(m, object_key(m, second['id'])) == []
    assert events(m, done['id']) == ['pending_delete', 'delete_failed', 'pending_delete']
    assert asset_state(m, second['id'])['status'] == 'deleted'


def test_worker_crash_is_resumed_and_a_stale_worker_is_fenced(d):
    m = d
    m.worker.drain()
    first, _ = upload(m, marker='one')
    second, _ = upload(m, marker='two')
    done = delete(m, 'class_session', m.classroom.id)
    queue = m.deploy.queue
    crashed = queue.claim('crashing-worker', 1)
    assert crashed is not None and str(crashed.request_id) == done['id'] and crashed.attempt == 1
    items = queue.inventory(crashed)
    assert len(items) == 2
    # It erases and records one object, then dies holding the lease.
    m.store.erase(items[0][1])
    queue.record(crashed, items[0][0], 'absent')
    assert queue.claim('impatient-worker', 30) is None
    time.sleep(1.3)
    successor = queue.claim('successor-worker', 30)
    assert successor is not None and successor.request_id == crashed.request_id
    assert successor.lease_token > crashed.lease_token and successor.attempt == 2
    assert [key for _, key in queue.inventory(successor)] == [items[1][1]]
    # The first worker wakes up: every write it attempts is refused.
    for late in (lambda: queue.inventory(crashed), lambda: queue.record(crashed, items[1][0], 'absent'),
                 lambda: queue.record(crashed, items[1][0], 'failed', 'STORAGE_UNAVAILABLE'), lambda: queue.finish(crashed, 0)):
        with pytest.raises(StaleDeletionFence):
            late()
    assert one(m, 'SELECT state FROM deletion_assets WHERE id=:id', id=items[1][0])['state'] == 'pending'
    m.store.erase(items[1][1])
    queue.record(successor, items[1][0], 'absent')
    assert queue.finish(successor, 0) == 'objects_absent'
    with pytest.raises(StaleDeletionFence):
        queue.finish(successor, 0)
    assert finished(m, done['id'])
    assert events(m, done['id']) == ['pending_delete']
    assert all(asset_state(m, a['id'])['status'] == 'deleted' for a in (first, second))


def test_settle_window_delays_erasure_and_removes_a_late_write(d):
    m = d
    m.worker.drain()
    slow = build(m.a, m.deploy, m.spool, policy=DeletionPolicy(write_settle=timedelta(seconds=4), retry_backoff=timedelta(0),
                                                                 max_backoff=timedelta(0), lease_seconds=30))
    with TestClient(slow.app, base_url='https://testserver', cookies=m.client.cookies) as client:
        original, m.client = m.client, client
        try:
            staged_data = content('in-flight')
            staged, _ = create(m, staged_data)
            late_key = object_key(m, staged['asset_id'])
            done = delete(m, 'class_session', m.classroom.id)
        finally:
            m.client = original
    assert done['state'] == 'pending_delete'
    # Nothing may be claimed while an upload admitted before revocation could still land.
    assert slow.worker.run_once() is False
    # That upload's object write arrives now, after access was revoked.
    m.store.client.put_object(Bucket=m.store.bucket, Key=late_key, Body=staged_data)
    assert m.store.inspect(late_key) is not None
    # Even a worker that somehow holds the request cannot record absence early.
    with m.pg.admin.begin() as c:
        c.execute(sa.text('UPDATE deletion_executions SET not_before=clock_timestamp() WHERE request_id=:id'), dict(id=done['id']))
    early = m.deploy.queue.claim('early-worker', 1)
    assert early is not None
    item = m.deploy.queue.inventory(early)[0]
    with pytest.raises(SettleWindowOpen):
        m.deploy.queue.record(early, item[0], 'absent')
    assert m.deploy.queue.finish(early, 0) == 'failed'
    assert m.store.inspect(late_key) is not None
    time.sleep(4.2)
    slow.worker.drain()
    assert finished(m, done['id'])
    assert versions(m, late_key) == [] and m.store.inspect(late_key) is None


def test_orphan_sweep_removes_revived_objects_and_never_guesses(d):
    m = d
    m.worker.drain()
    gone, _ = upload(m, marker='gone')
    done = delete(m, 'class_session', m.classroom.id)
    m.worker.drain()
    assert finished(m, done['id'])
    revived_key = object_key(m, gone['id'])
    room = classroom(m, 'Live synthetic classroom')
    live, live_data = upload(m, room)
    # A delayed write lands on a key whose deletion was already proven.
    m.store.client.put_object(Bucket=m.store.bucket, Key=revived_key, Body=b'late-synthetic-write')
    # An object no manifest names, and one that is not a generated media key at all.
    unreferenced = f'media/{uuid4().hex}/{uuid4().hex}/' + 'a' * 64
    m.store.client.put_object(Bucket=m.store.bucket, Key=unreferenced, Body=b'unreferenced-synthetic')
    m.store.client.put_object(Bucket=m.store.bucket, Key='media/not-generated.txt', Body=b'foreign-synthetic')
    # An unreferenced key whose latest version is a delete marker: a plain listing no
    # longer shows it, yet its bytes are still stored as an older version.
    hidden = f'media/{uuid4().hex}/{uuid4().hex}/' + 'c' * 64
    m.store.client.put_object(Bucket=m.store.bucket, Key=hidden, Body=b'hidden-synthetic')
    m.store.client.delete_object(Bucket=m.store.bucket, Key=hidden)
    assert len(versions(m, hidden)) == 2
    assert hidden not in [item['Key'] for item in m.store.client.list_objects_v2(Bucket=m.store.bucket, Prefix=hidden).get('Contents', [])]
    listed = [key for key, _ in m.store.keys(hidden[:-1], 10)]
    assert hidden in listed and all(key > hidden[:-1] for key in listed) and listed == sorted(set(listed))
    report = m.deletion.sweep_orphans()
    assert report['revived_removed'] == 1 and versions(m, revived_key) == []
    # Without the explicit switch an unreferenced object is counted and left in place.
    assert report['unreferenced'] >= 3 and report['unreferenced_removed'] == 0
    assert m.store.inspect(unreferenced) is not None and len(versions(m, hidden)) == 2
    assert m.deletion.sweep_orphans(purge_unreferenced=True, grace=timedelta(days=1))['unreferenced_removed'] == 0
    assert m.store.inspect(unreferenced) is not None
    purged = m.deletion.sweep_orphans(purge_unreferenced=True, grace=timedelta(0))
    assert purged['unreferenced_removed'] >= 1 and purged['foreign'] >= 1 and purged['failed'] == 0
    assert versions(m, unreferenced) == [] and versions(m, hidden) == []
    assert m.store.client.get_object(Bucket=m.store.bucket, Key='media/not-generated.txt')['Body'].read() == b'foreign-synthetic'
    # Live and pending manifests are never candidates.
    assert m.client.get(access(m, live)).content == live_data
    m.store.client.delete_object(Bucket=m.store.bucket, Key='media/not-generated.txt')


def test_expiry_uses_the_database_clock_and_ignores_account_disablement(d):
    m = d
    m.worker.drain()
    soon, soon_data = upload(m, marker='soon')
    kept, kept_data = upload(m, marker='kept')
    abandoned_data = content('abandoned')
    abandoned, _ = create(m, abandoned_data)
    assert put(m, abandoned, abandoned_data).status_code == 200
    path = access(m, soon)
    with m.pg.admin.begin() as c:
        c.execute(sa.text("UPDATE media_assets SET expires_at=clock_timestamp()+interval '2 seconds' WHERE id=:id"), dict(id=soon['id']))
    assert m.client.get(path).content == soon_data
    assert m.deletion.expire_due() == 0
    # The system path cannot expire an asset early, name another scope, or claim a user origin.
    live_intent = DeletionIntent.for_scope(m.uid, 'media_asset', UUID(soon['id']), 'expiry')
    with pytest.raises(sa.exc.DBAPIError) as early:
        m.deploy.queue.expire(live_intent, None, 0)
    assert early.value.orig.sqlstate == '23514'
    for wrong in (DeletionIntent.for_scope(m.uid, 'class_session', m.classroom.id, 'expiry'),
                  DeletionIntent.for_scope(m.uid, 'media_asset', UUID(soon['id']), 'user')):
        with pytest.raises(sa.exc.DBAPIError) as refused:
            m.deploy.queue.expire(wrong, None, 0)
        assert refused.value.orig.sqlstate == '42501'
    assert asset_state(m, soon['id'])['status'] == 'committed'
    time.sleep(2.3)
    assert m.client.get(path).status_code == 410
    # Disabling the account does not extend retention: the sweep needs no active owner.
    with m.pg.admin.begin() as c:
        c.execute(sa.text("UPDATE users SET state='disabled' WHERE id=:id"), dict(id=m.uid))
        c.execute(sa.text("UPDATE media_uploads SET expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"), dict(id=abandoned['id']))
    try:
        with pytest.raises(InactiveOwner):
            with m.pg.db.transaction(OwnerContext(m.uid)):
                pass
        assert m.deletion.expire_due() == 2
        assert m.deletion.expire_due() == 0
        requests = rows(m, """SELECT d.id,d.scope_type,d.scope_id,d.state,x.origin,x.ledger_generation FROM deletion_requests d
            JOIN deletion_executions x ON x.request_id=d.id WHERE d.owner_id=:o""", o=m.uid)
        assert sorted(str(r['scope_id']) for r in requests) == sorted([soon['id'], abandoned['asset_id']])
        assert all(r['scope_type'] == 'media_asset' and r['origin'] == 'expiry' and r['state'] == 'pending'
                   and r['ledger_generation'] for r in requests)
        m.worker.drain()
        assert all(one(m, 'SELECT state FROM deletion_requests WHERE id=:id', id=r['id'])['state'] == 'complete' for r in requests)
        assert versions(m, object_key(m, soon['id'])) == [] and versions(m, object_key(m, abandoned['asset_id'])) == []
    finally:
        with m.pg.admin.begin() as c:
            c.execute(sa.text("UPDATE users SET state='active' WHERE id=:id"), dict(id=m.uid))
    as_user(m, m.a.email)
    assert m.client.get(access(m, kept)).content == kept_data


def test_retention_needs_an_approved_policy_and_audio_commit_stays_closed(d):
    m = d
    asset, _ = upload(m)
    assert rows(m, 'SELECT 1 FROM retention_policies') == []

    def assign(version, lifetime, hard):
        with m.pg.admin.begin() as c:
            c.execute(sa.text(f"""UPDATE media_assets SET retention_policy_version=:v,retention_anchor_at=first_cloud_received_at,
                expires_at=first_cloud_received_at+interval '{lifetime}',hard_expire_at=first_cloud_received_at+interval '{hard}'
                WHERE id=:id"""), dict(v=version, id=asset['id']))
    # A policy label that nobody approved authorizes nothing.
    with pytest.raises(sa.exc.DBAPIError) as unknown:
        assign('pretend-approved', '1 hour', '1 hour')
    assert unknown.value.orig.sqlstate == '23514'
    version = 'synthetic-' + uuid4().hex[:12]
    with m.pg.admin.begin() as c:
        c.execute(sa.text("""INSERT INTO retention_policies(version,media_kind,max_lifetime,approval_reference,synthetic)
            VALUES(:v,'slide',interval '2 hours','synthetic test fixture, not a product decision',true),
                  (:a,'audio_chunk',interval '2 hours','synthetic test fixture, not a product decision',true)"""),
                  dict(v=version, a=version + '-audio'))
    try:
        for lifetime, hard in (('1 hour', '3 hours'), ('3 hours', '2 hours')):
            with pytest.raises(sa.exc.DBAPIError):
                assign(version, lifetime, hard)
        with pytest.raises(sa.exc.DBAPIError):
            assign(version + '-audio', '1 hour', '2 hours')  # approved for another media kind
        assign(version, '1 hour', '2 hours')
        with pytest.raises(sa.exc.DBAPIError):
            assign('unassigned', '1 hour', '2 hours')
        # Deadlines can still only move earlier (0009), and a policy does not open audio commit.
        with pytest.raises(sa.exc.DBAPIError):
            assign(version, '90 minutes', '2 hours')
        with pytest.raises(sa.exc.IntegrityError) as audio:
            with m.pg.admin.begin() as c:
                c.execute(sa.text("""INSERT INTO media_assets(id,owner_id,kind,sha256,size_bytes,mime_type,object_key,object_etag,
                    status,confirmed_at,first_cloud_received_at,retention_anchor_at,expires_at,hard_expire_at,retention_policy_version)
                    VALUES(:id,:owner,'audio_chunk',:hash,1,'audio/pcm',:key,'synthetic','committed',clock_timestamp(),clock_timestamp(),
                    clock_timestamp(),clock_timestamp()+interval '1 hour',clock_timestamp()+interval '1 hour',:policy)"""),
                    dict(id=uuid4(), owner=m.uid, hash='0' * 64, key=f'media/{m.uid.hex}/{uuid4().hex}/' + '0' * 64, policy=version + '-audio'))
        assert audio.value.orig.diag.constraint_name == 'media_audio_commit_not_enabled'
    finally:
        done = delete(m, 'class_session', m.classroom.id)
        m.worker.drain()
        assert finished(m, done['id'])


def test_audio_scope_deletes_only_audio_and_keeps_the_classroom(d):
    m = d
    slide, slide_data = upload(m)
    # Audio commit is disabled, so staged synthetic audio manifests stand in for chunks.
    audio_ids, audio_keys = [uuid4(), uuid4()], []
    with m.pg.admin.begin() as c:
        for asset_id in audio_ids:
            key = f'media/{m.uid.hex}/{uuid4().hex}/' + 'b' * 64
            audio_keys.append(key)
            c.execute(sa.text("""INSERT INTO media_assets(id,owner_id,class_session_id,kind,sha256,size_bytes,mime_type,object_key)
                VALUES(:id,:owner,:room,'audio_chunk',:hash,9,'audio/pcm',:key)"""),
                      dict(id=asset_id, owner=m.uid, room=m.classroom.id, hash='b' * 64, key=key))
            m.store.client.put_object(Bucket=m.store.bucket, Key=key, Body=b'synthetic')
    shown = preview(m, 'audio', m.classroom.id).json()
    assert shown['scope_type'] == 'audio' and shown['media_bytes'] == 18 and shown['class_session_count'] == 0
    done = delete(m, 'audio', m.classroom.id)
    assert done['scope_type'] == 'audio'
    assert all(asset_state(m, i)['status'] == 'delete_pending' for i in audio_ids)
    m.worker.drain()
    assert status(m, done['id'])['state'] == 'deleted' and status(m, done['id'])['physical_completed_at']
    assert events(m, done['id']) == ['pending_delete', 'deleted']
    # A completed request cannot be reopened, even by the migration identity.
    with pytest.raises(sa.exc.DBAPIError):
        with m.pg.admin.begin() as c:
            c.execute(sa.text("SELECT set_config('app.owner_id',:o,true)"), dict(o=str(m.uid)))
            c.execute(sa.text("UPDATE deletion_requests SET state='pending',completed_at=NULL WHERE id=:id"), dict(id=done['id']))
    assert all(versions(m, key) == [] for key in audio_keys)
    # The classroom, its slide and its text survive an audio deletion.
    with m.pg.db.transaction(OwnerContext(m.uid)) as tx:
        assert tx.catalog.get_class_session(m.classroom.id).deleted_at is None
    assert m.client.get(access(m, slide)).content == slide_data
    assert rows(m, 'SELECT 1 FROM deletion_tombstones WHERE owner_id=:o', o=m.uid) == []


def test_account_erasure_port_seals_revokes_and_scrubs_identity(d):
    m = d
    asset, _ = upload(m)
    key = object_key(m, asset['id'])
    package = dict(purpose='import_package', class_session_id=None, mime_type='application/json', byte_length=2,
                   sha256=hashlib.sha256(b'{}').hexdigest(), filename='synthetic.json')
    loose = m.client.post(BASE + '/media/uploads', json=package, headers=headers(m))
    assert loose.status_code == 201, loose.text
    # No HTTP route reaches account deletion in this package.
    assert m.client.post(BASE + '/me/delete', json={}, headers=headers(m)).status_code in (404, 405)
    assert m.client.get(BASE + '/me/deletion-preview').status_code in (404, 405)
    operation = dict(purpose='deleteAccount', target=dict(kind='account', id=str(m.uid)),
                     candidate_parameters=dict(idempotency_key=str(uuid4()), body={}))
    assert m.client.post(BASE + '/auth/reauth', json=dict(password=PASSWORD, operation=operation), headers=headers(m)).status_code in (422, 503)
    # An account tombstone cannot commit unless the same transaction seals the account.
    with pytest.raises(sa.exc.DBAPIError):
        with m.pg.db.transaction(OwnerContext(m.uid)) as tx:
            request = uuid4()
            tx.connection.execute(sa.text("""INSERT INTO deletion_requests(id,owner_id,scope_type,scope_id,state,access_revoked_at)
                VALUES(:r,:o,'account',:o,'access_revoked',clock_timestamp())"""), dict(r=request, o=m.uid))
            tx.connection.execute(sa.text("""INSERT INTO deletion_tombstones(id,owner_id,request_id,resource_type,resource_id,erasure_epoch,ledger_sequence)
                VALUES(gen_random_uuid(),:o,:r,'account',:o,1,1)"""), dict(r=request, o=m.uid))
    assert m.client.get(BASE + '/auth/session').status_code == 200
    request_id = m.deletion.erase_account(OwnerContext(m.uid))
    user = one(m, 'SELECT state,security_epoch,normalized_email FROM users WHERE id=:id', id=m.uid)
    assert user['state'] == 'erasing'
    # Sessions, new logins and internal owner contexts are all refused at once.
    assert m.client.get(BASE + '/auth/session').status_code == 401
    assert rows(m, 'SELECT 1 FROM auth_sessions WHERE owner_id=:o AND revoked_at IS NULL', o=m.uid) == []
    m.client.cookies.clear()
    csrf = m.client.get(BASE + '/auth/csrf').json()['csrf_token']
    denied = m.client.post(BASE + '/auth/login', json=dict(email=m.a.email, password=PASSWORD, remember_me=False, device_name='after erasure'),
                           headers={'Origin': 'https://testserver', 'Sec-Fetch-Site': 'same-origin', 'X-CSRF-Token': csrf, 'Idempotency-Key': str(uuid4())})
    assert denied.status_code == 401
    with pytest.raises(InactiveOwner):
        with m.pg.db.transaction(OwnerContext(m.uid)):
            pass
    assert one(m, "SELECT resource_type FROM deletion_tombstones WHERE owner_id=:o", o=m.uid)['resource_type'] == 'account'
    assert len(rows(m, 'SELECT 1 FROM deletion_assets WHERE request_id=:r', r=request_id)) == 2
    assert all(r['status'] == 'delete_pending' for r in rows(m, 'SELECT status FROM media_assets WHERE owner_id=:o', o=m.uid))
    # Repeating the call, or reconciling, changes nothing.
    assert m.deletion.erase_account(OwnerContext(m.uid)) == request_id
    assert m.deletion.reconcile()['unresolved'] == []
    m.worker.drain()
    erased = one(m, 'SELECT state,deleted_at,normalized_email,password_hash,email_verified_at,profile,preferences FROM users WHERE id=:id', id=m.uid)
    assert erased['state'] == 'erased' and erased['deleted_at'] is not None
    assert erased['normalized_email'] is None and erased['password_hash'] is None and erased['email_verified_at'] is None
    assert erased['profile'] == {} and erased['preferences'] == {}
    assert settled(m, request_id)
    assert versions(m, key) == []
    assert m.deploy.queue.audit()['erasing_accounts'] == 0


def test_ledger_chain_is_create_only_contended_safely_and_tamper_evident(deploy):
    services = deploy.services
    client = services.ledger.client()

    def fresh_bucket():
        name = 'pr04b-chain-' + uuid4().hex
        client.create_bucket(Bucket=name)
        return name

    bucket = fresh_bucket()
    writers = [ledger_store(services, bucket) for _ in range(4)]
    intents = [DeletionIntent.for_scope(uuid4(), 'class_session', uuid4()) for _ in range(12)]
    with ThreadPoolExecutor(8) as pool:
        generations = list(pool.map(lambda pair: writers[pair[0] % 4].append(pair[1]).generation, enumerate(intents * 2)))
    # Contending writers never overwrite each other: 12 intents, 12 distinct contiguous positions.
    assert sorted(set(generations)) == list(range(1, 13))
    assert generations[:12] == generations[12:] or sorted(generations[:12]) == sorted(generations[12:])
    chain = ledger_store(services, bucket).entries()
    assert [e.generation for e in chain] == list(range(1, 13)) and {e.intent for e in chain} == set(intents)
    assert writers[0].append(intents[3]) == next(e for e in chain if e.intent == intents[3])
    listed = client.list_objects_v2(Bucket=bucket, Prefix='ledger/v1/')['Contents']
    assert len(listed) == 12
    body = client.get_object(Bucket=bucket, Key=listed[0]['Key'])['Body'].read()
    assert b'title' not in body and set(json.loads(body)['intent']) == set(intents[0].record())
    # A lost entry is a gap, not a shorter history.
    client.delete_object(Bucket=bucket, Key=listed[5]['Key'])
    with pytest.raises(LedgerCorrupt):
        ledger_store(services, bucket).entries()
    with pytest.raises(LedgerCorrupt):
        writers[1].append(DeletionIntent.for_scope(uuid4(), 'course', uuid4()))
    # A rewritten entry breaks the chain for every reader.
    bucket = fresh_bucket()
    writer = ledger_store(services, bucket)
    for intent in intents[:3]:
        writer.append(intent)
    forged = json.loads(client.get_object(Bucket=bucket, Key='ledger/v1/00000000000000000002.json')['Body'].read())
    forged['intent'] = DeletionIntent.for_scope(uuid4(), 'semester', uuid4()).record()
    client.put_object(Bucket=bucket, Key='ledger/v1/00000000000000000002.json',
                      Body=json.dumps(forged, sort_keys=True, separators=(',', ':')).encode())
    with pytest.raises(LedgerCorrupt):
        ledger_store(services, bucket).entries()
    with pytest.raises(LedgerCorrupt):
        writer.entries()
    # A truncated history (entries removed from the end) is detected by a reader that saw them.
    bucket = fresh_bucket()
    writer = ledger_store(services, bucket)
    for intent in intents[:3]:
        writer.append(intent)
    witness = writer.head()
    assert witness[0] == 3 and writer.verify_head(witness) and writer.verify_head((0, '0' * 64))
    client.delete_object(Bucket=bucket, Key='ledger/v1/00000000000000000003.json')
    with pytest.raises(LedgerCorrupt):
        writer.entries()
    # The writer does not hand out the position of an entry the store no longer holds.
    with pytest.raises(LedgerCorrupt):
        writer.append(intents[2])
    # A reader that never saw the lost entry finds a shorter chain that is valid in itself;
    # only a head witness kept outside the store exposes the loss.
    fresh = ledger_store(services, bucket)
    assert len(fresh.entries()) == 2
    with pytest.raises(LedgerCorrupt):
        fresh.verify_head(witness)
    with pytest.raises(LedgerCorrupt):
        fresh.verify_head((2, witness[1]))
    for malformed in (None, (1,), ('1', witness[1]), (-1, witness[1]), (1, 'not-a-hash')):
        with pytest.raises(ValueError):
            fresh.verify_head(malformed)
    # Foreign objects in the ledger prefix, and an unreachable store, keep it closed.
    client.put_object(Bucket=bucket, Key='ledger/v1/unexpected', Body=b'x')
    with pytest.raises(LedgerCorrupt):
        ledger_store(services, bucket).entries()
    from classroom_web.infrastructure.deletion_ledger import S3DeletionLedger
    with pytest.raises(LedgerUnavailable):
        S3DeletionLedger(endpoint='http://127.0.0.1:9', bucket=bucket, region='us-east-1', access_key='x', secret_key='y', synthetic_loopback=True)


def test_service_stays_closed_until_reconciled_and_when_the_ledger_is_behind(d):
    m = d
    upload(m)
    done = delete(m, 'class_session', m.classroom.id)
    unreconciled = build(m.a, m.deploy, m.spool, reconcile=False)
    with TestClient(unreconciled.app, base_url='https://testserver', cookies=m.client.cookies) as client:
        for path in ('/auth/session', '/auth/csrf', f'/deletions/{done["id"]}', f'/class-sessions/{m.classroom.id}/deletion-preview'):
            reply = client.get(BASE + path)
            assert reply.status_code == 503 and reply.json()['error']['code'] == 'SERVICE_DISABLED'
        assert client.get(BASE + f'/media/{uuid4()}/content?ticket=x').status_code == 503
        unreconciled.deletion.reconcile()
        assert client.get(BASE + '/auth/session').status_code == 200
    # A ledger that lost history (here: an empty one) contradicts the database. Stay closed.
    empty = 'pr04b-empty-' + uuid4().hex
    m.deploy.services.ledger.client().create_bucket(Bucket=empty)
    behind = build(m.a, m.deploy, m.spool, ledger=ledger_store(m.deploy.services, empty), reconcile=False)
    with pytest.raises(LedgerCorrupt):
        behind.deletion.reconcile()
    assert behind.deletion.ready is False
    with TestClient(behind.app, base_url='https://testserver', cookies=m.client.cookies) as client:
        assert client.get(BASE + '/auth/session').status_code == 503
    # The ledger must not be the media bucket.
    from classroom_web.application.deletion import DeletionService
    with pytest.raises(ValueError):
        DeletionService(auth=m.a.service, database=m.pg.db, queue=m.deploy.queue, store=m.store,
                        ledger=SimpleNamespace(bucket=m.store.bucket, endpoint=m.deploy.services.media.endpoint),
                        confirmation_secret=b'x' * 32)
    m.worker.drain()


def test_confirmed_version_and_idempotency_key_are_held_across_the_ledger_append(d):
    m = d
    upload(m)
    with m.pg.db.transaction(OwnerContext(m.uid)) as tx:
        elsewhere = tx.catalog.create_course(m.semester.id, 'Another synthetic course')
    other_room = classroom(m, 'Second synthetic classroom')
    other_asset, other_data = upload(m, other_room, 'other')
    shown, other = preview(m, 'class_session', m.classroom.id).json(), preview(m, 'class_session', other_room.id).json()
    key = str(uuid4())
    ledger, seen = m.deletion.ledger, {}
    real = ledger.append

    def move():
        try:
            with m.pg.db.transaction(OwnerContext(m.uid)) as tx:
                tx.catalog.move_class_session(m.classroom.id, m.classroom.revision, elsewhere.id)
            seen['move'] = 'moved'
        except Exception as error:  # noqa: BLE001  (the outcome is asserted below)
            seen['move'] = type(error).__name__

    with ThreadPoolExecutor(2) as pool:
        def during_append(intent, **options):
            # The intent is on its way to the ledger. Now: an edit of the confirmed
            # classroom, and a second request reusing the key for another classroom.
            if 'mover' not in seen:
                seen['mover'] = pool.submit(move)
                seen['rival'] = pool.submit(submit, m, 'class_session', other_room.id, other, key)
                time.sleep(0.4)
                seen['waiting'] = not seen['mover'].done() and not seen['rival'].done()
            return real(intent, **options)

        ledger.append = during_append
        try:
            accepted = submit(m, 'class_session', m.classroom.id, shown, key)
        finally:
            del ledger.append
        seen['mover'].result(15)
        rival = seen['rival'].result(15)
    assert accepted.status_code == 202, accepted.text
    # Both waited for the deletion to commit instead of slipping in beside it.
    assert seen['waiting'] is True
    # The edit met the revoked classroom; what was deleted is the confirmed version.
    assert seen['move'] == 'Gone'
    tombstone = one(m, 'SELECT deleted_revision FROM deletion_tombstones WHERE owner_id=:o AND resource_id=:r', o=m.uid, r=m.classroom.id)
    assert tombstone['deleted_revision'] == shown['revision'] + 1
    assert one(m, 'SELECT course_id FROM class_sessions WHERE id=:id', id=m.classroom.id)['course_id'] == m.course.id
    # The reused key was refused before anything about its target was recorded anywhere.
    assert rival.status_code == 409 and rival.json()['error']['code'] == 'IDEMPOTENCY_MISMATCH'
    refused = DeletionIntent.for_scope(m.uid, 'class_session', other_room.id)
    assert [e for e in ledger.entries() if e.intent.intent_id == refused.intent_id] == []
    assert rows(m, 'SELECT 1 FROM deletion_requests WHERE owner_id=:o AND scope_id=:s', o=m.uid, s=other_room.id) == []
    # So neither crash recovery nor a restore can ever delete it on the refused request's behalf.
    report = m.deletion.reconcile()
    assert report['applied'] == [] and report['accepted'] == [] and m.deletion.ready
    assert m.client.get(access(m, other_asset)).content == other_data
    assert asset_state(m, other_asset['id'])['status'] == 'committed'
    m.worker.drain()


def test_stalled_ledger_still_revokes_locally_within_the_request_deadline(d):
    m = d
    asset, _ = upload(m)
    proxy = StallProxy(m.deploy.services.ledger.endpoint)
    try:
        slow = build(m.a, m.deploy, m.spool, ledger=ledger_store(m.deploy.services, m.deploy.ledger_bucket, proxy.endpoint))
        with TestClient(slow.app, base_url='https://testserver', cookies=m.client.cookies) as client:
            original, m.client = m.client, client
            try:
                shown = preview(m, 'class_session', m.classroom.id).json()
                # The ledger store keeps accepting connections and answers nothing.
                proxy.stalled = True
                started = time.monotonic()
                refused = submit(m, 'class_session', m.classroom.id, shown, str(uuid4()))
                elapsed = time.monotonic() - started
            finally:
                m.client = original
        assert refused.status_code == 503 and refused.json()['error']['code'] == 'STORAGE_UNAVAILABLE', refused.text
        assert 1 <= elapsed < 5
        # Not accepted, but access is already denied: the revocation was not lost to the deadline.
        request = one(m, """SELECT d.id,d.state,x.ledger_generation FROM deletion_requests d
            JOIN deletion_executions x ON x.request_id=d.id WHERE d.owner_id=:o AND d.scope_id=:s""", o=m.uid, s=m.classroom.id)
        assert request['state'] == 'access_revoked' and request['ledger_generation'] is None
        assert m.client.post(BASE + f'/media/{asset["id"]}/access', json={}, headers=headers(m)).status_code == 410
        intent = DeletionIntent.for_scope(m.uid, 'class_session', m.classroom.id)
        proxy.stalled = False
        assert [e for e in m.deploy.ledger.entries() if e.intent.intent_id == intent.intent_id] == []
    finally:
        proxy.close()
    report = m.deletion.reconcile()
    assert len(report['accepted']) == 1 and report['unresolved'] == [] and m.deletion.ready
    m.worker.drain()
    assert finished(m, request['id'])
