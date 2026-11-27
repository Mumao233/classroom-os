"""Capture admission with the real switch installation, owner leases and HTTP routes."""
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from uuid import uuid4

import pytest
import sqlalchemy as sa

from deletion_support import as_user, auth, classroom, d, deploy, pg, services
from test_authentication import BASE
from test_billing_integration import fee_control
from test_capture_recovery import acquire, acquire_body, capture, command, headers, read_manifests


def write_switch(db, actor, enabled):
    return db.scalar(sa.text("""SELECT billing_api.update_switches(:actor,:op,
        (SELECT revision FROM billing_control.switches WHERE singleton),
        jsonb_build_object('new_capture',CAST(:enabled AS boolean)),:reason)"""),
        dict(actor=actor, op=uuid4(), enabled=enabled, reason='Synthetic capture admission'))


@pytest.fixture
def recording(capture, fee_control):
    with capture.pg.admin.begin() as db:
        write_switch(db, fee_control.actor, True)
    return capture


def request_acquire(m, room=None, **changes):
    return m.client.post(BASE + f'/class-sessions/{room or m.classroom.id}/capture/acquire',
                         json=acquire_body(m, **changes), headers=headers(m))


def assert_disabled(response):
    assert response.status_code == 503, response.text
    assert response.json()['error']['code'] == 'SERVICE_DISABLED'


def test_existing_lease_renew_takeover_and_paused_resume_after_close(recording, fee_control):
    m = recording
    key = str(uuid4())
    first = acquire(m, key)
    with m.pg.admin.begin() as db:
        write_switch(db, fee_control.actor, False)
    assert acquire(m, key) == first
    renewed = command(m, first, 'renew')
    assert renewed.status_code == 200 and renewed.json()['id'] == first['id'], renewed.text

    old_device = m.session['device_id']
    as_user(m, m.a.email)
    assert m.session['device_id'] != old_device
    conflict = request_acquire(m, confirm_takeover=True, expected_lease_id=str(uuid4()))
    assert conflict.status_code == 409 and conflict.json()['error']['conflict_id'] == first['id']
    taken = acquire(m, confirm_takeover=True, expected_lease_id=first['id'])
    assert taken['fence_token'] == first['fence_token'] + 1
    assert command(m, taken, 'pause').status_code == 200
    resume_key = str(uuid4())
    resumed = command(m, taken, 'resume', key=resume_key)
    assert resumed.status_code == 200, resumed.text
    active = resumed.json()
    assert active['fence_token'] == taken['fence_token'] + 1
    assert command(m, taken, 'resume', key=resume_key).json() == active
    assert [item['capture_epoch_id'] for item in read_manifests(m).json()['items']] == [
        item['capture_epoch_id'] for item in (first, taken, active)]


@pytest.mark.parametrize('start', ['fresh', 'cross-classroom', 'expired', 'stopped'])
def test_closed_gate_refuses_new_epochs_and_reopen_restores_acquire(recording, fee_control, start):
    m = recording
    room = m.classroom.id
    changes = {}
    if start != 'fresh':
        old = acquire(m)
        if start == 'stopped':
            assert command(m, old, 'stop', manifest_sha256='a' * 64, known_gaps=[]).status_code == 200
        else:
            changes = dict(confirm_takeover=True, expected_lease_id=old['id'])
            if start == 'cross-classroom':
                room = classroom(m).id
            else:
                assert command(m, old, 'pause').status_code == 200
                with m.pg.admin.begin() as db:
                    db.execute(sa.text("""UPDATE capture_leases SET expires_at=clock_timestamp()-interval '1 second'
                        WHERE owner_id=:owner"""), dict(owner=m.uid))
    with m.pg.admin.begin() as db:
        write_switch(db, fee_control.actor, False)
    if start == 'expired':
        for action in ('renew', 'resume'):
            rejected = command(m, old, action)
            assert rejected.status_code == 409 and rejected.json()['error']['code'] == 'STALE_FENCE', rejected.text
    assert_disabled(request_acquire(m, room, **changes))
    with m.pg.admin.begin() as db:
        assert db.scalar(sa.text('SELECT count(*) FROM capture_epochs WHERE owner_id=:owner'),
                         dict(owner=m.uid)) == (0 if start == 'fresh' else 1)
        if start != 'fresh':
            assert str(db.scalar(sa.text('SELECT id FROM capture_leases WHERE owner_id=:owner'),
                                 dict(owner=m.uid))) == old['id']
        write_switch(db, fee_control.actor, True)
    opened = request_acquire(m, room, **changes)
    assert opened.status_code == 201 and opened.json()['class_session_id'] == str(room), opened.text


def wait_for_blocked(pg, blocker_pid, waiting):
    deadline = monotonic() + 8
    with pg.admin.connect() as monitor:
        while monotonic() < deadline:
            monitor.exec_driver_sql('SELECT pg_stat_clear_snapshot()')
            if monitor.scalar(sa.text("""SELECT EXISTS (SELECT FROM pg_stat_activity
                    WHERE datname=current_database() AND :blocker=ANY(pg_blocking_pids(pid)))"""),
                    dict(blocker=blocker_pid)):
                return
            assert not waiting.done(), 'the competing operation completed before the blocker released its lock'
            sleep(0.01)
    pytest.fail('PostgreSQL did not report the switch/epoch lock dependency')


def test_uncommitted_close_blocks_first_acquire_until_new_value_is_visible(recording, fee_control):
    m = recording
    with ThreadPoolExecutor(max_workers=1) as pool:
        with m.pg.admin.begin() as db:
            write_switch(db, fee_control.actor, False)
            blocker_pid = db.scalar(sa.text('SELECT pg_backend_pid()'))
            waiting = pool.submit(request_acquire, m)
            wait_for_blocked(m.pg, blocker_pid, waiting)
        assert_disabled(waiting.result(timeout=10))
    with m.pg.admin.connect() as db:
        counts = db.execute(sa.text("""SELECT
            (SELECT count(*) FROM capture_epochs WHERE owner_id=:owner),
            (SELECT count(*) FROM capture_leases WHERE owner_id=:owner),
            (SELECT count(*) FROM consent_records WHERE owner_id=:owner),
            (SELECT count(*) FROM operation_receipts WHERE owner_id=:owner AND operation='acquireCapture')"""),
            dict(owner=m.uid)).one()
        assert tuple(counts) == (0, 0, 0, 0)


def test_admitted_epoch_holds_switch_lock_until_commit(recording, fee_control):
    m = recording
    room = str(m.classroom.id)

    def close():
        with m.pg.admin.begin() as db:
            write_switch(db, fee_control.actor, False)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with m.capture.transaction(m.ctx, room) as (repo, principal, policy):
            lease = m.capture.acquire_in_transaction(repo, principal, policy, room, str(uuid4()), acquire_body(m))
            blocker_pid = repo.tx.connection.scalar(sa.text('SELECT pg_backend_pid()'))
            waiting = pool.submit(close)
            wait_for_blocked(m.pg, blocker_pid, waiting)
        waiting.result(timeout=10)
    assert command(m, lease, 'renew').status_code == 200
    assert_disabled(request_acquire(m, classroom(m).id, confirm_takeover=True, expected_lease_id=lease['id']))


def test_reader_has_no_switch_table_access_and_missing_function_blocks_new_admission(recording):
    m = recording
    existing = acquire(m)
    with m.pg.admin.begin() as db:
        rights = db.execute(sa.text("""SELECT
            has_function_privilege(:login,'billing_api.new_capture_enabled()','EXECUTE'),
            has_table_privilege(:login,'billing_control.switches','SELECT,UPDATE'),
            has_function_privilege(:login,'billing_api.update_switches(uuid,uuid,bigint,jsonb,text)','EXECUTE')
            """), dict(login=m.pg.login)).one()
        assert tuple(rights) == (True, False, False)
        db.exec_driver_sql('ALTER FUNCTION billing_api.new_capture_enabled() RENAME TO synthetic_missing_capture_flag')
    try:
        assert_disabled(request_acquire(m, confirm_takeover=True, expected_lease_id=existing['id']))
        assert [item['capture_epoch_id'] for item in read_manifests(m).json()['items']] == [
            existing['capture_epoch_id']]
    finally:
        with m.pg.admin.begin() as db:
            db.exec_driver_sql('ALTER FUNCTION billing_api.synthetic_missing_capture_flag() RENAME TO new_capture_enabled')
