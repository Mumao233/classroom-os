"""HTTP 0.4.0 administrator writes through restricted gateways and real PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from classroom_web.application.identity import token_digest
from classroom_web.domain.identity import IdentityRejected
from classroom_web.infrastructure.auth_crypto import random_token
from classroom_web.infrastructure.identity_repository import PgIdentityRepository
from classroom_web.test_support import SYNTHETIC_PASSWORD
from test_authentication import BASE, PASSWORD, auth
from test_billing_integration import fee_api, fee_control, headers, pg, prove, sign_in


pytestmark = pytest.mark.parametrize('fee_api', ['formal'], indirect=True)


@pytest.fixture
def admin(fee_api):
    fee_api.session, fee_api.seed = sign_in(fee_api, administrator=True)
    return fee_api


@pytest.fixture
def registration(pg):
    login = 'pr02a_pr11b_registration_' + uuid4().hex[:12]
    with pg.admin.begin() as db:
        db.exec_driver_sql(f'CREATE ROLE "{login}" LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB')
        db.exec_driver_sql(f'GRANT classroom_web_identity TO "{login}"')
    url = pg.runtime_url.set(username=login, password=SYNTHETIC_PASSWORD)
    repo = PgIdentityRepository(url.render_as_string(hide_password=False))
    try:
        yield repo
    finally:
        repo.dispose()
        with pg.admin.begin() as db:
            db.exec_driver_sql(f'DROP ROLE "{login}"')


def proof(api, purpose, body, kind, target):
    return prove(api, api.session, api.seed, purpose, body, str(uuid4()),
                 target={'kind': kind, 'id': str(target)})


def write(api, method, path, body, credential):
    return api.client.request(method, BASE + path, json=body, headers=headers(api.session, **credential))


def rejected(response, status, code):
    assert response.status_code == status and response.json()['error']['code'] == code, response.text


def audit(api, verb, target):
    with api.c.pg.admin.connect() as db:
        return db.execute(sa.text('''SELECT created_at,reason FROM identity_control.identity_control_audit
            WHERE actor_id=:actor AND action=:verb AND target_id=:target'''),
            dict(actor=api.a.uid, verb=verb, target=target)).mappings().all()


def invitation(api, *, state='open', expired=False):
    invitation_id, original = uuid4(), random_token()
    with api.c.pg.admin.begin() as db:
        db.execute(sa.text('''INSERT INTO invitations(id,created_by,status,activated_user_id,token_hash,expires_at)
            VALUES(:id,:actor,:state,:used,:digest,clock_timestamp()+make_interval(days => :days))'''),
            dict(id=invitation_id, actor=api.a.uid, state=state, used=api.a.uid if state == 'consumed' else None,
                 digest=token_digest(original), days=-1 if expired else 1))
    return invitation_id, original


def create_student(api, state='active'):
    uid = uuid4()
    email = uid.hex + '@example.test'
    with api.c.pg.admin.begin() as db:
        db.execute(sa.text('''INSERT INTO users(id,normalized_email,password_hash,role,state,email_verified_at)
            SELECT :id,:email,password_hash,'student',:state,clock_timestamp() FROM users WHERE id=:admin'''),
            dict(id=uid, email=email, state=state, admin=api.a.uid))
    return uid, email


def test_disable_user_binds_path_body_and_key_and_revokes_every_session(admin):
    target, email = create_student(admin)
    other, _ = create_student(admin)
    body = dict(base_revision=1, status='disabled', reason='  收回合成测试账号  ')
    path = '/admin/users/' + str(target)
    credential = proof(admin, 'adminUpdateUser', body, 'user', target)
    # fee_api owns the app lifespan. The two independent browsers own only their cookies and transports.
    with (closing(TestClient(admin.client.app, base_url='https://testserver')) as first,
          closing(TestClient(admin.client.app, base_url='https://testserver')) as second):
        for device, client in enumerate((first, second)):
            csrf = client.get(BASE + '/auth/csrf').json()
            response = client.post(BASE + '/auth/login', headers=headers(csrf),
                json=dict(email=email, password=PASSWORD, remember_me=False, device_name=f'Synthetic {device}'))
            assert response.status_code == 200 and response.json()['state'] == 'authenticated'
            assert client.get(BASE + '/auth/session').status_code == 200

        for wrong_path, wrong_body, wrong_proof in (
            ('/admin/users/' + str(other), body, credential),
            (path, {**body, 'reason': '替换理由'}, credential),
            (path, body, {**credential, 'Idempotency-Key': str(uuid4())}),
        ):
            rejected(write(admin, 'PATCH', wrong_path, wrong_body, wrong_proof), 403, 'REAUTH_REQUIRED')
        response = write(admin, 'PATCH', path, body, credential)
        assert response.status_code == 200, response.text
        result = response.json()
        assert (result['id'], result['status'], result['revision'], result['email']) == (str(target), 'disabled', 2, email)
        assert first.get(BASE + '/auth/session').status_code == second.get(BASE + '/auth/session').status_code == 401

    assert write(admin, 'PATCH', path, body, credential).json() == result
    rejected(write(admin, 'PATCH', path, {**body, 'reason': '不同操作'}, credential), 409, 'IDEMPOTENCY_MISMATCH')
    rejected(write(admin, 'PATCH', '/admin/users/' + str(other), body, credential), 409, 'IDEMPOTENCY_MISMATCH')
    rows = audit(admin, 'adminUpdateUser', target)
    assert len(rows) == 1 and rows[0]['reason'] == body['reason'] and rows[0]['created_at'] is not None
    with admin.c.pg.admin.connect() as db:
        assert db.execute(sa.text('''SELECT count(*), count(revoked_at) FROM auth_sessions WHERE owner_id=:id'''),
                          dict(id=target)).one() == (2, 2)
        assert db.scalar(sa.text('SELECT state FROM users WHERE id=:id'), dict(id=other)) == 'active'


@pytest.mark.parametrize('case,status,code', [
    ('self', 403, 'FORBIDDEN'), ('erasing', 410, 'GONE'), ('pending', 422, 'VALIDATION_FAILED'),
    ('stale', 409, 'REVISION_CONFLICT'), ('absent', 404, 'NOT_FOUND'),
])
def test_user_transition_guards(admin, case, status, code):
    if case == 'pending':
        target = uuid4()
        invite_id, _ = invitation(admin)
        with admin.c.pg.admin.begin() as db:
            db.execute(sa.text('''INSERT INTO pending_registrations
                (id,normalized_email,password_hash,invitation_id,email_proof_hash,expires_at)
                SELECT :id,:email,password_hash,:invite,:digest,clock_timestamp()+interval '1 hour'
                FROM users WHERE id=:actor'''), dict(id=target, email=target.hex+'@example.test',
                invite=invite_id, digest=token_digest(random_token()), actor=admin.a.uid))
    elif case == 'self':
        target = admin.a.uid
    elif case == 'absent':
        target = uuid4()
    else:
        target, _ = create_student(admin, 'erasing' if case == 'erasing' else 'active')
    body = dict(base_revision=2 if case == 'stale' else 1, status='disabled', reason='Synthetic state transition')
    credential = proof(admin, 'adminUpdateUser', body, 'user', target)
    rejected(write(admin, 'PATCH', '/admin/users/' + str(target), body, credential), status, code)
    assert audit(admin, 'adminUpdateUser', target) == []


def test_concurrent_invitation_create_returns_one_secret_and_one_receipt(admin, registration):
    email = uuid4().hex + '@example.test'
    body = dict(kind='email_bound', email=email, reason='  合成邀请批次  ')
    credential = proof(admin, 'adminCreateInvitation', body, 'account', admin.a.uid)
    rendezvous = Barrier(2)

    def send():
        rendezvous.wait(timeout=10)
        return write(admin, 'POST', '/admin/invitations', body, credential)

    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = [pool.submit(send) for _ in range(2)]
        results = sorted((attempt.result(timeout=15) for attempt in attempts), key=lambda response: response.status_code)
    created, replay = results
    assert created.status_code == 201, created.text
    rejected(replay, 409, 'SECRET_ALREADY_ISSUED')
    token, row = created.json()['one_time_token'], created.json()['invitation']
    assert (row['state'], row['kind'], row['revision']) == ('issued', 'email_bound', 1)
    assert row['email_hint'] and row['email_hint'] != email
    rejected(write(admin, 'POST', '/admin/invitations', {**body, 'reason': '另一理由'}, credential),
             409, 'IDEMPOTENCY_MISMATCH')
    assert token not in replay.text
    with admin.c.pg.admin.connect() as db:
        invitation_row = db.execute(sa.text('SELECT token_hash,email_binding FROM invitations WHERE id=:id'),
                                    dict(id=row['id'])).one()
        assert bytes(invitation_row.token_hash) == token_digest(token) and invitation_row.email_binding == email
        assert db.execute(sa.text('''SELECT secret,result FROM preauth_receipts WHERE context_hash=:actor AND
            operation='adminCreateInvitation' AND operation_key=:op'''),
            dict(actor=str(admin.a.uid), op=credential['Idempotency-Key'])).one() == (True, None)
        stored_audit = db.scalar(sa.text('''SELECT row_to_json(a)::text FROM identity_control.identity_control_audit a
            WHERE actor_id=:actor AND target_id=:id AND action='adminCreateInvitation' '''),
            dict(actor=admin.a.uid, id=row['id']))
    assert token not in stored_audit and email not in stored_audit
    rows = audit(admin, 'adminCreateInvitation', row['id'])
    assert len(rows) == 1 and rows[0]['reason'] == body['reason']
    phc = admin.a.crypto.hash(PASSWORD)
    with pytest.raises(IdentityRejected, match='INVITATION_UNAVAILABLE'):
        registration.register('other-'+email, phc, token_digest(token), token_digest(random_token()), {})
    assert registration.register(email, phc, token_digest(token), token_digest(random_token()), {})


@pytest.mark.parametrize('action,expired', [('revoke', False), ('reissue', False), ('reissue', True)])
def test_invitation_rotation_and_revocation_use_the_registration_token(admin, registration, action, expired):
    purpose = 'adminReissueInvitation' if action == 'reissue' else 'adminRevokeInvitation'
    invite, old_token = invitation(admin)
    email, phc, mail_proof = uuid4().hex+'@example.test', admin.a.crypto.hash(PASSWORD), token_digest(random_token())
    registration.register(email, phc, token_digest(old_token), mail_proof, {})
    if expired:
        with admin.c.pg.admin.begin() as db:
            db.execute(sa.text("UPDATE invitations SET expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"),
                       dict(id=invite))
    body = dict(reason='  Synthetic invitation change  ')
    credential = proof(admin, purpose, body, 'invitation', invite)
    path = f'/admin/invitations/{invite}/{action}'
    rejected(write(admin, 'POST', f'/admin/invitations/{uuid4()}/{action}', body, credential), 403, 'REAUTH_REQUIRED')
    changed = write(admin, 'POST', path, body, credential)
    assert changed.status_code == 200, changed.text
    row = changed.json()['invitation'] if action == 'reissue' else changed.json()
    assert row['id'] == str(invite) and row['revision'] == 2
    replay = write(admin, 'POST', path, body, credential)
    if action == 'reissue':
        token = changed.json()['one_time_token']
        rejected(replay, 409, 'SECRET_ALREADY_ISSUED')
        assert row['state'] == 'issued' and token != old_token and token not in replay.text
        with admin.c.pg.admin.connect() as db:
            assert db.execute(sa.text('''SELECT secret,result FROM preauth_receipts
                WHERE context_hash=:actor AND operation=:action AND operation_key=:op'''),
                dict(actor=str(admin.a.uid), action=purpose, op=credential['Idempotency-Key'])).one() == (True, None)
        assert registration.register(email, phc, token_digest(token), token_digest(random_token()), {})
    else:
        assert row['state'] == 'revoked' and replay.status_code == 200 and replay.json() == row
    with pytest.raises(IdentityRejected, match='INVITATION_UNAVAILABLE'):
        registration.register(uuid4().hex+'@example.test', phc, token_digest(old_token), token_digest(random_token()), {})
    with pytest.raises(IdentityRejected, match='VERIFICATION_INVALID|INVITATION_UNAVAILABLE'):
        registration.activate(mail_proof)
    rows = audit(admin, purpose, invite)
    assert len(rows) == 1 and rows[0]['reason'] == body['reason']


@pytest.mark.parametrize('action,state,expired', [
    ('revoke', 'open', True), ('revoke', 'consumed', False), ('revoke', 'revoked', False),
    ('reissue', 'consumed', False), ('reissue', 'revoked', False),
])
def test_invitation_states_reject_unavailable_transitions(admin, action, state, expired):
    invite, _ = invitation(admin, state=state, expired=expired)
    body = dict(reason='Synthetic invitation state check')
    purpose = 'adminReissueInvitation' if action == 'reissue' else 'adminRevokeInvitation'
    credential = proof(admin, purpose, body, 'invitation', invite)
    response = write(admin, 'POST', f'/admin/invitations/{invite}/{action}', body, credential)
    rejected(response, 409, 'INVITATION_UNAVAILABLE')
    with admin.c.pg.admin.connect() as db:
        assert db.scalar(sa.text('SELECT revision FROM invitations WHERE id=:id'), dict(id=invite)) == 1
    assert audit(admin, purpose, invite) == []


def test_failed_invitation_audit_rolls_back_secret_receipt_and_proof(admin):
    body = dict(kind='single_use', reason='Synthetic audit transaction')
    credential = proof(admin, 'adminCreateInvitation', body, 'account', admin.a.uid)
    table = 'identity_control.identity_control_audit'
    with admin.c.pg.admin.begin() as db:
        db.exec_driver_sql(f"""ALTER TABLE {table} ADD CONSTRAINT pr11b_fixture_reject
            CHECK (actor_id <> '{admin.a.uid}'::uuid OR action <> 'adminCreateInvitation') NOT VALID""")
    try:
        failed = write(admin, 'POST', '/admin/invitations', body, credential)
        assert failed.status_code == 503 and 'one_time_token' not in failed.text
        with admin.c.pg.admin.connect() as db:
            assert db.scalar(sa.text('SELECT count(*) FROM invitations WHERE created_by=:id'), dict(id=admin.a.uid)) == 0
            params = dict(op=credential['Idempotency-Key'])
            assert db.scalar(sa.text('SELECT count(*) FROM preauth_receipts WHERE operation_key=:op'), params) == 0
            assert db.execute(sa.text('SELECT consumed_at FROM reauth_proofs WHERE operation_key=:op'), params).scalar_one() is None
    finally:
        with admin.c.pg.admin.begin() as db:
            db.exec_driver_sql(f'ALTER TABLE {table} DROP CONSTRAINT pr11b_fixture_reject')
    assert write(admin, 'POST', '/admin/invitations', body, credential).status_code == 201


def test_secret_receipt_requires_a_current_session_proof_and_consumes_a_fresh_one(admin):
    body = dict(kind='single_use', reason='Synthetic lost response')
    credential = proof(admin, 'adminCreateInvitation', body, 'account', admin.a.uid)
    path, key = '/admin/invitations', credential['Idempotency-Key']
    assert write(admin, 'POST', path, body, credential).status_code == 201
    with admin.c.pg.admin.begin() as db:
        db.execute(sa.text("""UPDATE reauth_proofs SET verified_at=clock_timestamp()-interval '3 minutes',
            expires_at=clock_timestamp()-interval '1 second' WHERE owner_id=:actor AND operation_key=:key"""),
            dict(actor=admin.a.uid, key=key))
    rejected(write(admin, 'POST', path, body, credential), 403, 'REAUTH_REQUIRED')
    assert admin.client.post(BASE + '/auth/logout', json={}, headers=headers(admin.session)).status_code == 200
    with admin.c.pg.admin.begin() as db:
        db.execute(sa.text("""UPDATE mfa_credentials SET state='revoked',revoked_at=clock_timestamp()
            WHERE owner_id=:owner AND state='active'"""), {'owner': admin.a.uid})
    admin.session, admin.seed = sign_in(admin, administrator=True)
    rejected(write(admin, 'POST', path, body, credential), 403, 'REAUTH_REQUIRED')
    fresh = prove(admin, admin.session, admin.seed, 'adminCreateInvitation', body, key)
    rejected(write(admin, 'POST', path, body, fresh), 409, 'SECRET_ALREADY_ISSUED')
    with admin.c.pg.admin.connect() as db:
        assert db.execute(sa.text('''SELECT count(*),count(consumed_at) FROM reauth_proofs
            WHERE owner_id=:owner AND operation_key=:key'''), dict(owner=admin.a.uid, key=key)).one() == (2, 2)
        assert db.scalar(sa.text('SELECT count(*) FROM invitations WHERE created_by=:id'), {'id': admin.a.uid}) == 1
