"""Upgrade and HTTP replay of PR-11b quota adjustments on real PostgreSQL."""
from contextlib import closing
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from classroom_web.entrypoints.http import create_app
from test_authentication import BASE, auth
from test_billing_integration import ROOT, fee_api, fee_control, headers, pg, prove, sign_in


PURPOSE = 'adminAdjustQuota'
PATH = BASE + '/admin/quota-adjustments'
VERSION = 'classroom-admin-0.4.0-sql-2'
MAXIMUM = 2**53 - 1


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
@pytest.mark.parametrize(('signature', 'old_comment'), [
    ('billing_api.adjust_quota(uuid,uuid,uuid,bigint,text)', 'NULL'),
    ('billing_api.apply_admin(jsonb)', "'classroom-admin-0.4.0-sql-1'"),
])
def test_old_installed_sql_refuses_admin_mount_and_keeps_auth_available(fee_api, signature, old_comment):
    api = fee_api
    options = dict(auth=api.a.service, origin='https://testserver', contract=api.contract)
    with api.c.pg.admin.begin() as db:
        assert db.scalar(sa.text("SELECT obj_description(to_regprocedure(:name),'pg_proc')"),
                         dict(name=signature)) == VERSION
        db.exec_driver_sql(f'COMMENT ON FUNCTION {signature} IS {old_comment}')
    try:
        with pytest.raises(ValueError, match=VERSION):
            create_app(**options, billing_admin=api.admin)
        with closing(TestClient(create_app(**options), base_url='https://testserver')) as client:
            assert client.get(BASE + '/auth/csrf').status_code == 200
    finally:
        with api.c.pg.admin.begin() as db:
            db.exec_driver_sql(f"COMMENT ON FUNCTION {signature} IS '{VERSION}'")
    create_app(**options, billing_admin=api.admin)


def test_safe_integer_preflight_rejects_existing_overflow_until_explicitly_resolved(fee_control):
    control = fee_control
    period = uuid4()
    script = (ROOT / 'tools/pr11a/check_quota_upgrade.sql').read_text(encoding='utf-8')
    insert = sa.text("""INSERT INTO quota_periods(id,owner_id,resource,period_start,period_end,limit_value)
        VALUES(:id,:owner,'interactive_month_count','2001-01-01','2001-02-01',:limit)""")
    overflow = dict(id=period, owner=control.actor, limit=MAXIMUM + 1)
    # The uncommitted legacy fixture and DDL changes are discarded on connection close.
    with control.pg.admin.connect() as db:
        with pytest.raises(sa.exc.IntegrityError) as error, db.begin_nested():
            db.execute(insert, overflow)
        assert error.value.orig.diag.constraint_name == 'quota_limit_safe_integer'
        db.exec_driver_sql('ALTER TABLE quota_periods DROP CONSTRAINT quota_limit_safe_integer')
        db.execute(insert, overflow)
        with pytest.raises(sa.exc.DBAPIError) as error, db.begin_nested():
            db.exec_driver_sql(script, execution_options={'no_parameters': True})
        assert error.value.orig.sqlstate == '22003'
        assert error.value.orig.diag.message_primary == 'QUOTA_UPGRADE_REQUIRES_RECONCILIATION'
        assert db.scalar(sa.text('SELECT limit_value FROM quota_periods WHERE id=:id'), dict(id=period)) == MAXIMUM + 1
        db.execute(sa.text('UPDATE quota_periods SET limit_value=:approved WHERE id=:id'),
                   dict(approved=MAXIMUM, id=period))
        db.exec_driver_sql(script, execution_options={'no_parameters': True})
        assert db.scalar(sa.text('SELECT limit_value FROM quota_periods WHERE id=:id'), dict(id=period)) == MAXIMUM

    with pytest.raises(sa.exc.DBAPIError) as error, control.pg.db.engine.begin() as scoped:
        scoped.exec_driver_sql(script, execution_options={'no_parameters': True})
    assert error.value.orig.sqlstate == '42501'
    assert error.value.orig.diag.message_primary == 'QUOTA_UPGRADE_REQUIRES_GLOBAL_VISIBILITY'


def quota_body(api, recipient):
    response = api.client.get(BASE + f'/admin/users/{recipient}/quota')
    assert response.status_code == 200, response.text
    bucket = next(item for item in response.json()['buckets'] if item['kind'] == 'interactive_month_count')
    body = dict(user_id=str(recipient), bucket_kind=bucket['kind'], period_start=bucket['period_start'],
                additional_units=3, reason='Synthetic quota reconciliation')
    return body, bucket['limit']


def apply(api, session, body, proof):
    return api.client.post(PATH, json=body, headers=headers(session, **proof))


def fresh_login(api, session):
    response = api.client.post(BASE + '/auth/logout', json={}, headers=headers(session))
    assert response.status_code == 200, response.text
    with api.c.pg.admin.begin() as db:
        db.execute(sa.text("""UPDATE mfa_credentials SET state='revoked',revoked_at=clock_timestamp()
            WHERE owner_id=:actor AND state='active'"""), dict(actor=api.a.uid))
    return sign_in(api, administrator=True)


def assert_error(response, status, code):
    assert response.status_code == status and response.json()['error']['code'] == code, response.text


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
@pytest.mark.parametrize('old_receipt', ['account', 'single-session', 'matching-sessions', 'conflicting-sessions'])
def test_quota_replay_checks_original_body_across_logins(fee_api, old_receipt):
    api = fee_api
    session, seed = sign_in(api, administrator=True)
    key = str(uuid4())
    body, initial_limit = quota_body(api, api.a.uid)
    instant = datetime.fromisoformat(body['period_start'].replace('Z', '+00:00'))
    alternate = instant.isoformat(timespec='microseconds')
    if alternate == body['period_start']:
        alternate = instant.isoformat(timespec='seconds')
    assert alternate != body['period_start'] and datetime.fromisoformat(alternate) == instant
    altered = {**body, 'period_start': alternate}
    proof = prove(api, session, seed, PURPOSE, body, key)
    first = apply(api, session, body, proof)
    assert first.status_code == 201, first.text
    params = dict(key=key, actor=str(api.a.uid), owner=api.a.uid, purpose=PURPOSE, device=session['device_id'])
    with api.c.pg.admin.begin() as db:
        original = db.execute(sa.text("""SELECT context_hash,commitment,result FROM preauth_receipts
            WHERE operation=:purpose AND operation_key=:key"""), params).mappings().one()
        assert original['context_hash'] == params['actor'] and original['result'] == {'user_id': params['actor']}
        if old_receipt != 'account':
            db.execute(sa.text("""UPDATE preauth_receipts SET context_hash=(
                SELECT token_hash FROM auth_sessions WHERE owner_id=:owner AND device_id=:device)
                WHERE context_hash=:actor AND operation=:purpose AND operation_key=:key"""), params)
    session, seed = fresh_login(api, session)
    if old_receipt in ('matching-sessions', 'conflicting-sessions'):
        old_binding = original['commitment']
        if old_receipt == 'conflicting-sessions':
            altered_operation = dict(purpose=PURPOSE, target=dict(kind='account', id=params['actor']),
                                     candidate_parameters=dict(idempotency_key=key, body=altered))
            old_binding = api.a.crypto.commitment('operation', altered_operation)
        with api.c.pg.admin.begin() as db:
            db.execute(sa.text("""INSERT INTO preauth_receipts(context_hash,operation,operation_key,commitment,result)
                SELECT token_hash,:purpose,:key,:binding,jsonb_build_object('user_id',CAST(:actor AS text))
                FROM auth_sessions WHERE owner_id=:owner AND device_id=:device"""),
                       {**params, 'device': session['device_id'], 'binding': old_binding})
    if old_receipt == 'conflicting-sessions':
        fresh = prove(api, session, seed, PURPOSE, body, key)
        assert_error(apply(api, session, body, fresh), 503, 'RESULT_UNKNOWN')
    else:
        assert_error(apply(api, session, body, proof), 403, 'REAUTH_REQUIRED')
        changed_proof = prove(api, session, seed, PURPOSE, altered, key)
        assert_error(apply(api, session, altered, changed_proof), 409, 'IDEMPOTENCY_MISMATCH')
        session, seed = fresh_login(api, session)
        fresh = prove(api, session, seed, PURPOSE, body, key)
        replay = apply(api, session, body, fresh)
        assert replay.status_code == 201 and replay.json() == first.json(), replay.text
    assert quota_body(api, api.a.uid)[1] == initial_limit + body['additional_units']
    with api.c.pg.admin.connect() as db:
        assert db.scalar(sa.text('SELECT count(*) FROM quota_adjustments WHERE op_id=:key'), params) == 1
        assert db.scalar(sa.text("""SELECT count(*) FROM billing_control.audit WHERE action='quota_adjustment'
            AND actor_id=:owner AND op_id=:key"""), params) == 1
        canonical = db.scalar(sa.text("""SELECT commitment FROM preauth_receipts
            WHERE context_hash=:actor AND operation=:purpose AND operation_key=:key"""), params)
        assert canonical == (None if old_receipt == 'conflicting-sessions' else original['commitment'])


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
def test_audit_only_quota_adjustment_returns_unknown_without_applying_again(fee_api):
    api = fee_api
    session, seed = sign_in(api, administrator=True)
    key = str(uuid4())
    body, initial_limit = quota_body(api, api.a.uid)
    proof = prove(api, session, seed, PURPOSE, body, key)
    assert apply(api, session, body, proof).status_code == 201
    with api.c.pg.admin.begin() as db:
        db.execute(sa.text('DELETE FROM preauth_receipts WHERE operation=:op AND operation_key=:key'),
                   dict(op=PURPOSE, key=key))
    session, seed = fresh_login(api, session)
    fresh = prove(api, session, seed, PURPOSE, body, key)
    assert_error(apply(api, session, body, fresh), 503, 'RESULT_UNKNOWN')
    assert quota_body(api, api.a.uid)[1] == initial_limit + body['additional_units']
    with api.c.pg.admin.connect() as db:
        assert db.scalar(sa.text('SELECT count(*) FROM quota_adjustments WHERE op_id=:key'), dict(key=key)) == 1
        assert db.scalar(sa.text('SELECT count(*) FROM preauth_receipts WHERE operation=:purpose AND operation_key=:key'),
                         dict(key=key, purpose=PURPOSE)) == 0


@pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
def test_independent_admins_can_adjust_same_bucket_with_same_key(fee_api):
    api = fee_api
    session, seed = sign_in(api, administrator=True)
    key, second_actor = str(uuid4()), uuid4()
    body, initial_limit = quota_body(api, api.a.uid)
    proof = prove(api, session, seed, PURPOSE, body, key)
    assert apply(api, session, body, proof).status_code == 201
    email = second_actor.hex + '@example.test'
    with api.c.pg.admin.begin() as db:
        db.execute(sa.text("""INSERT INTO users(id,normalized_email,password_hash,role,state,email_verified_at)
            SELECT :id,:email,password_hash,'admin','active',clock_timestamp() FROM users WHERE id=:source"""),
                   dict(id=second_actor, email=email, source=api.a.uid))
    with closing(TestClient(api.client.app, base_url='https://testserver')) as client:
        other = SimpleNamespace(a=SimpleNamespace(uid=second_actor, email=email, crypto=api.a.crypto, pg=api.c.pg),
                                client=client)
        other_session, other_seed = sign_in(other, administrator=True)
        other_proof = prove(other, other_session, other_seed, PURPOSE, body, key)
        result = apply(other, other_session, body, other_proof)
        assert result.status_code == 201, result.text
    assert quota_body(api, api.a.uid)[1] == initial_limit + 2 * body['additional_units']
    with api.c.pg.admin.connect() as db:
        actors = {str(api.a.uid), str(second_actor)}
        assert set(db.scalars(sa.text("""SELECT context_hash FROM preauth_receipts
            WHERE operation=:purpose AND operation_key=:key"""), dict(purpose=PURPOSE, key=key))) == actors
        assert set(db.scalars(sa.text("""SELECT admin_actor_id::text FROM quota_adjustments WHERE op_id=:key"""),
                              dict(key=key))) == actors
        assert set(db.scalars(sa.text("""SELECT actor_id::text FROM billing_control.audit
            WHERE action='quota_adjustment' AND op_id=:key"""), dict(key=key))) == actors
