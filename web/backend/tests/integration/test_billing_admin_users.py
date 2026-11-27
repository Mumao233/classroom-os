"""Formal administrator user discovery and state reconciliation on PostgreSQL."""
from contextlib import closing
from types import SimpleNamespace
from uuid import uuid4

import pyotp
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from classroom_web.application.identity import token_digest
from classroom_web.infrastructure.auth_crypto import random_token
from test_admin_identity_writes import admin, audit, auth, create_student, fee_api, invitation, proof, registration, rejected, write
from test_authentication import BASE, PASSWORD, install_factor, mfa_login
from test_billing_admin_capacity import fee_control, pg
from test_billing_integration import headers, sign_in


pytestmark = pytest.mark.parametrize('fee_api', ['formal'], indirect=True)
PATH = '/admin/users'
USER_FIELDS = {'id', 'revision', 'created_at', 'updated_at', 'email', 'nickname', 'school', 'major',
               'role', 'status', 'mfa_enabled'}


def test_user_pages_project_metadata_and_scope_the_cursor(admin, registration):
    student, email = create_student(admin, 'disabled')
    deleting, _ = create_student(admin, 'erasing')
    create_student(admin, 'erased')
    other, other_email = create_student(admin)
    _, token = invitation(admin)
    pending_email = uuid4().hex + '@example.test'
    pending = registration.register(pending_email, admin.a.crypto.hash(PASSWORD), token_digest(token),
                                    token_digest(random_token()), {'nickname': 'Awaiting verification'})
    with admin.c.pg.admin.begin() as db:
        db.execute(sa.text("""UPDATE users SET profile='{"nickname":"Test","school":"School","major":"Math"}'
                             WHERE id=:id"""), {'id': student})
        # Identical creation times force the UUID tie-breaker on every page.
        db.execute(sa.text("UPDATE users SET created_at='2024-10-01T00:00:00Z'"))
        db.execute(sa.text("UPDATE pending_registrations SET created_at='2024-10-01T00:00:00Z' WHERE id=:id"),
                   {'id': pending})

    expected_ids = sorted(map(str, (admin.a.uid, student, deleting, other, pending)))
    rows = {}
    params = {'limit': 2}
    first_cursor = None
    for start in range(0, len(expected_ids), 2):
        response = admin.client.get(BASE + PATH, params=params)
        assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store', response.text
        page = response.json()
        assert set(page) == {'items', 'next_cursor'}
        assert [row['id'] for row in page['items']] == expected_ids[start:start + 2]
        for row in page['items']:
            assert set(row) == USER_FIELDS
            rows[row['id']] = row
        if start + 2 < len(expected_ids):
            assert isinstance(page['next_cursor'], str) and 0 < len(page['next_cursor']) <= 512
            first_cursor = first_cursor or page['next_cursor']
            params['cursor'] = page['next_cursor']
        else:
            assert page['next_cursor'] is None
    assert (rows[str(admin.a.uid)]['role'], rows[str(admin.a.uid)]['mfa_enabled']) == ('admin', True)
    assert {key: rows[str(student)][key] for key in ('status', 'email', 'nickname', 'school', 'major', 'mfa_enabled')} == {
        'status': 'disabled', 'email': email, 'nickname': 'Test', 'school': 'School', 'major': 'Math', 'mfa_enabled': False}
    assert rows[str(deleting)]['status'] == 'deleting'
    assert (rows[str(pending)]['status'], rows[str(pending)]['email'], rows[str(pending)]['nickname']) == (
        'pending', pending_email, 'Awaiting verification')
    for invalid in ({'cursor': first_cursor + 'x'}, {'limit': 0}, {'limit': 101}, {'offset': 1},
                    [('limit', '1'), ('limit', '2')]):
        rejected(admin.client.get(BASE + PATH, params=invalid), 422, 'VALIDATION_FAILED')
    rejected(admin.client.get(BASE + '/admin/invitations', params={'cursor': first_cursor}), 422, 'VALIDATION_FAILED')
    with closing(TestClient(admin.client.app, base_url='https://testserver')) as client:
        second = SimpleNamespace(client=client, a=SimpleNamespace(uid=other, email=other_email,
                                 crypto=admin.a.crypto, pg=admin.c.pg))
        sign_in(second, administrator=True)
        assert client.get(BASE + PATH).status_code == 200
        rejected(client.get(BASE + PATH, params={'cursor': first_cursor}), 422, 'VALIDATION_FAILED')


def test_listed_revision_drives_patch_and_replay_returns_restored_user(admin):
    target, email = create_student(admin)
    other, other_email = create_student(admin)
    with admin.c.pg.admin.begin() as db:
        db.execute(sa.text('UPDATE users SET revision=7 WHERE id=:id'), {'id': target})
    page = admin.client.get(BASE + PATH)
    assert page.status_code == 200, page.text
    row = next(row for row in page.json()['items'] if row['email'] == email)
    path = PATH + '/' + row['id']
    disable = dict(base_revision=row['revision'], status='disabled', reason='Suspend synthetic account')
    credential = proof(admin, 'adminUpdateUser', disable, 'user', row['id'])
    result = write(admin, 'PATCH', path, disable, credential)
    assert result.status_code == 200 and result.json()['status'] == 'disabled', result.text
    assert result.json()['revision'] == row['revision'] + 1 == 8
    with closing(TestClient(admin.client.app, base_url='https://testserver')) as client:
        second = SimpleNamespace(client=client, a=SimpleNamespace(uid=other, email=other_email,
                                 crypto=admin.a.crypto, pg=admin.c.pg))
        second.session, second.seed = sign_in(second, administrator=True)
        restore = dict(base_revision=result.json()['revision'], status='active', reason='Restore synthetic account')
        restored = write(second, 'PATCH', path, restore, proof(second, 'adminUpdateUser', restore, 'user', row['id']))
        assert restored.status_code == 200 and restored.json()['status'] == 'active', restored.text
        assert restored.json()['revision'] == 9

    replay = write(admin, 'PATCH', path, disable, credential)
    assert replay.status_code == 200 and replay.json() == restored.json(), replay.text
    assert len(audit(admin, 'adminUpdateUser', row['id'])) == 1


def test_user_list_requires_admin_and_current_factor(fee_api):
    api = fee_api
    _, seed = install_factor(api.a, role='student')
    mfa_login(SimpleNamespace(client=api.client, email=api.a.email), pyotp.TOTP(seed).now())
    rejected(api.client.get(BASE + PATH), 403, 'FORBIDDEN')
    with api.c.pg.admin.begin() as db:
        db.execute(sa.text("UPDATE users SET role='admin' WHERE id=:id"), {'id': api.a.uid})
        db.execute(sa.text("UPDATE mfa_credentials SET state='revoked',revoked_at=clock_timestamp() WHERE owner_id=:id"),
                   {'id': api.a.uid})
    rejected(api.client.get(BASE + PATH), 401, 'UNAUTHENTICATED')
    api.client.cookies.clear()
    csrf = api.client.get(BASE + '/auth/csrf').json()
    login = api.client.post(BASE + '/auth/login', headers=headers(csrf),
        json=dict(email=api.a.email, password=PASSWORD, remember_me=False, device_name='Synthetic admin'))
    rejected(login, 403, 'MFA_REQUIRED')
    rejected(api.client.get(BASE + PATH), 401, 'UNAUTHENTICATED')
