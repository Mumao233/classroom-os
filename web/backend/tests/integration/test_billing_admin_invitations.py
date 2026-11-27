"""Formal invitation discovery and one-time secret recovery over HTTP and PostgreSQL."""
from contextlib import closing
import time
from types import SimpleNamespace
from uuid import uuid4

import pyotp
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from classroom_web.application.identity import token_digest
from classroom_web.domain.identity import IdentityRejected
from classroom_web.infrastructure.auth_crypto import random_token
from test_admin_identity_writes import admin, create_student, invitation, proof, registration, rejected, write
from test_authentication import BASE, PASSWORD, auth, install_factor, mfa_login
from test_billing_integration import fee_api, fee_control, headers, pg, sign_in


pytestmark = pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
PATH = '/admin/invitations'


def test_lost_create_response_recovers_id_from_metadata_and_rotates_secret(admin, registration):
    email = f'synthetic@{uuid4().hex}.example.test'
    body = dict(kind='email_bound', email=email, reason='Synthetic invitation recovery')
    credential = proof(admin, 'adminCreateInvitation', body, 'account', admin.a.uid)
    lost = write(admin, 'POST', PATH, body, credential)
    assert lost.status_code == 201
    replay = write(admin, 'POST', PATH, body, credential)
    rejected(replay, 409, 'SECRET_ALREADY_ISSUED')

    page = admin.client.get(BASE + PATH)
    assert page.status_code == 200 and page.headers['Cache-Control'] == 'no-store'
    matching = [row for row in page.json()['items'] if row['email_hint'] == 's***@' + email.split('@')[1]]
    assert len(matching) == 1
    row = matching[0]
    assert set(row) == {'id', 'revision', 'created_at', 'updated_at', 'kind', 'email_hint', 'state', 'expires_at'}
    assert (row['kind'], row['state'], row['revision']) == ('email_bound', 'issued', 1)
    action = dict(reason='Replace the lost synthetic invitation')
    # A second action requires a genuine, later TOTP step for the same administrator.
    with admin.c.pg.admin.connect() as db:
        last = db.scalar(sa.text("SELECT last_accepted_step FROM mfa_credentials WHERE owner_id=:id AND state='active'"),
                         dict(id=admin.a.uid))
    time.sleep(max(0, last * 30 - time.time() + 0.1))
    credential = proof(admin, 'adminReissueInvitation', action, 'invitation', row['id'])
    rotated = write(admin, 'POST', f"{PATH}/{row['id']}/reissue", action, credential)
    assert rotated.status_code == 200, rotated.text
    assert (rotated.json()['invitation']['id'], rotated.json()['invitation']['revision']) == (row['id'], 2)

    # Only the verifier inspects the lost reply. Neither recovery request used its contents.
    old, new = lost.json()['one_time_token'], rotated.json()['one_time_token']
    assert old != new
    for response in (page, replay, admin.client.get(BASE + PATH)):
        for private in (old, new, token_digest(old).hex(), token_digest(new).hex(), email):
            assert private not in response.text
    phc = admin.a.crypto.hash(PASSWORD)
    with pytest.raises(IdentityRejected, match='INVITATION_UNAVAILABLE'):
        registration.register(email, phc, token_digest(old), token_digest(random_token()), {})
    assert registration.register(email, phc, token_digest(new), token_digest(random_token()), {})


def test_invitation_pages_are_stable_bounded_and_scoped_to_admin(admin):
    expected_states = {}
    for stored, expired, public in (('open', False, 'issued'), ('open', True, 'expired'),
                                    ('consumed', False, 'consumed'), ('revoked', False, 'revoked')):
        invitation_id, _ = invitation(admin, state=stored, expired=expired)
        expected_states[str(invitation_id)] = public
    with admin.c.pg.admin.begin() as db:
        db.execute(sa.text("UPDATE invitations SET created_at='2020-01-01 00:00:00+00' WHERE created_by=:actor"),
                   dict(actor=admin.a.uid))
        expected_ids = list(db.scalars(sa.text('SELECT id::text FROM invitations ORDER BY created_at DESC,invitations.id DESC')))

    seen = []
    params = {'limit': 1}
    first_cursor = None
    for expected in expected_ids:
        response = admin.client.get(BASE + PATH, params=params)
        assert response.status_code == 200, response.text
        rows, cursor = response.json()['items'], response.json()['next_cursor']
        assert len(rows) == 1 and rows[0]['id'] == expected
        if expected in expected_states:
            assert (rows[0]['kind'], rows[0]['email_hint'], rows[0]['state']) == ('single_use', None, expected_states[expected])
        seen.append(expected)
        if len(seen) < len(expected_ids):
            assert isinstance(cursor, str) and 1 <= len(cursor) <= 512
            params['cursor'] = cursor
            first_cursor = first_cursor or cursor
        else:
            assert cursor is None
    assert set(expected_states).issubset(seen)
    for invalid in ({'cursor': first_cursor + 'x'}, {'cursor': 'x' * 513}, {'limit': 0}, {'limit': 101},
                    [('limit', '1'), ('limit', '2')], {'offset': 1}):
        rejected(admin.client.get(BASE + PATH, params=invalid), 422, 'VALIDATION_FAILED')

    other_id, email = create_student(admin)
    with closing(TestClient(admin.client.app, base_url='https://testserver')) as client:
        other = SimpleNamespace(client=client, a=SimpleNamespace(uid=other_id, email=email,
                                crypto=admin.a.crypto, pg=admin.c.pg))
        sign_in(other, administrator=True)
        assert client.get(BASE + PATH).status_code == 200
        rejected(client.get(BASE + PATH, params={'cursor': first_cursor}), 422, 'VALIDATION_FAILED')


def test_invitation_list_rejects_mfa_student_and_admin_without_factor(fee_api):
    api = fee_api
    _, seed = install_factor(api.a, role='student')
    mfa_login(SimpleNamespace(client=api.client, email=api.a.email), pyotp.TOTP(seed).now())
    rejected(api.client.get(BASE + PATH), 403, 'FORBIDDEN')
    with api.c.pg.admin.begin() as db:
        db.execute(sa.text("UPDATE users SET role='admin' WHERE id=:id"), dict(id=api.a.uid))
        db.execute(sa.text("UPDATE mfa_credentials SET state='revoked',revoked_at=clock_timestamp() WHERE owner_id=:id"),
                   dict(id=api.a.uid))
    api.client.cookies.clear()
    csrf = api.client.get(BASE + '/auth/csrf').json()
    login = api.client.post(BASE + '/auth/login', headers=headers(csrf),
        json=dict(email=api.a.email, password=PASSWORD, remember_me=False, device_name='Synthetic admin'))
    rejected(login, 403, 'MFA_REQUIRED')
    rejected(api.client.get(BASE + PATH), 401, 'UNAUTHENTICATED')
