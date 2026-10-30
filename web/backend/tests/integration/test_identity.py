"""Synthetic identity workflows through a restricted real PostgreSQL service role."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
import pytest
import sqlalchemy as sa

from classroom_web.application.identity import IdentityService
from classroom_web.domain.data import InactiveOwner, NotFound, OwnerContext
from classroom_web.domain.identity import AuthorizationDenied, IdentityRejected, Principal
from classroom_web.infrastructure.database import Database
from classroom_web.infrastructure.identity_mail import CapturingMailAdapter
from classroom_web.infrastructure.identity_repository import PgIdentityRepository
from classroom_web.infrastructure.identity_passwords import Argon2PasswordHasher
from classroom_web.infrastructure.identity_schema import tables as identity_tables
from classroom_web.infrastructure.schema import tables
from classroom_web.test_support import synthetic_connect_args, validate_test_database_target

from conftest import migration_config


PASSWORD = "Synthetic-password-03a"


def principal(user_id, role="student", epoch=1):
    """Trusted test authentication adapter; never an application login shortcut."""
    now = datetime.now(timezone.utc)
    return Principal(user_id=user_id, role=role, security_epoch=epoch,
                     session_id=uuid4(), device_id=uuid4(), mfa_level="totp",
                     reauthenticated_at=now, expires_at=now + timedelta(minutes=30))


@pytest.fixture
def identity(pg):
    marker = uuid4().hex
    name, ilogin, rlogin = ("pr02a_identity_" + marker,
                            "pr02a_identity_login_" + marker,
                            "pr02a_owner_login_" + marker)
    target = validate_test_database_target(pg.admin_url.render_as_string(hide_password=False))
    control = sa.create_engine(target, isolation_level="AUTOCOMMIT", poolclass=sa.pool.NullPool,
                               connect_args=synthetic_connect_args())
    dburl = target.set(database=name)
    admin = repository = runtime = None
    created = False
    try:
        with control.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}" TEMPLATE template0 ENCODING \'UTF8\'')
        created = True
        admin = sa.create_engine(dburl, poolclass=sa.pool.NullPool,
                                 connect_args=synthetic_connect_args())
        cfg = migration_config(dburl.render_as_string(hide_password=False))
        with admin.connect() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
        with control.connect() as connection:
            for login, role in ((ilogin, "classroom_web_identity"),
                                (rlogin, "classroom_web_runtime")):
                connection.exec_driver_sql(f'CREATE ROLE "{login}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
                connection.exec_driver_sql(f'GRANT {role} TO "{login}"')
        admin_id = uuid4()
        with admin.begin() as connection:
            connection.execute(tables["users"].insert().values(id=admin_id,
                normalized_email="synthetic-admin@example.test", role="admin", state="active",
                password_hash=Argon2PasswordHasher().hash(PASSWORD),
                email_verified_at=sa.func.clock_timestamp()))
        iurl = dburl.set(username=ilogin)
        rurl = dburl.set(username=rlogin)
        repository = PgIdentityRepository(iurl.render_as_string(hide_password=False))
        runtime = Database(rurl.render_as_string(hide_password=False))
        service = IdentityService(repository, Argon2PasswordHasher(),
                                  admin_reauthentication_window=timedelta(minutes=5),
                                  resend_interval=timedelta(0))
        yield SimpleNamespace(admin=admin, repository=repository, service=service,
                              runtime=runtime, admin_principal=principal(admin_id, "admin"),
                              admin_id=admin_id, iurl=iurl, rurl=rurl, cfg=cfg)
    finally:
        if repository is not None:
            repository.dispose()
        if runtime is not None:
            runtime.dispose()
        if admin is not None:
            admin.dispose()
        if created:
            with control.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
                connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{ilogin}"')
                connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{rlogin}"')
        control.dispose()


def queued(identity, email="synthetic-student@example.test", invite=None):
    invite = invite or identity.service.create_invitation(identity.admin_principal, kind="single_use")
    pending = identity.service.register(email, PASSWORD, invite.token)
    mail = CapturingMailAdapter()
    while identity.service.dispatch_mail(mail):
        pass
    matching = [token for recipient, token in mail.messages if recipient == email]
    assert matching
    return invite, pending, matching[-1]


def activated(identity, email="synthetic-student@example.test"):
    invite, pending, token = queued(identity, email)
    user_id = identity.service.activate(token)
    return principal(user_id), invite, pending, token


def rows(identity, name):
    with identity.admin.connect() as connection:
        return list(connection.execute(sa.select(identity_tables[name])).mappings())


def test_registration_waits_for_email_and_stores_only_argon2id_and_digests(identity):
    invite, pending_id, token = queued(identity)
    pending = rows(identity, "pending_registrations")[0]
    assert pending["id"] == pending_id
    assert pending["password_hash"].startswith("$argon2id$")
    assert PASSWORD not in pending["password_hash"]
    assert pending["email_proof_hash"] == hashlib.sha256(token.encode()).digest()
    assert len(pending["email_proof_hash"]) == 32
    invitation = rows(identity, "invitations")[0]
    assert invitation["token_hash"] == hashlib.sha256(invite.token.encode()).digest()
    assert timedelta(days=7) - timedelta(seconds=10) <= invitation["expires_at"] - invitation["created_at"] <= timedelta(days=7, seconds=10)
    assert timedelta(minutes=30) - timedelta(seconds=10) <= pending["expires_at"] - datetime.now(timezone.utc) <= timedelta(minutes=30, seconds=10)
    with identity.admin.connect() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(tables["users"])) == 1
    with pytest.raises((AuthorizationDenied, IdentityRejected)):
        with identity.runtime.authorized_transaction(principal(pending_id)):
            pytest.fail("pending registration acquired learning access")
    for name in ("identity_mail_intents", "identity_audit", "identity_events"):
        stored = repr(rows(identity, name))
        assert token not in stored and invite.token not in stored and PASSWORD not in stored


def test_two_verified_pending_accounts_race_one_invite_and_only_one_activates(identity):
    invite = identity.service.create_invitation(identity.admin_principal, kind="single_use")
    _, first, token_a = queued(identity, "synthetic-a@example.test", invite)
    _, second, token_b = queued(identity, "synthetic-b@example.test", invite)
    barrier = Barrier(2)
    def compete(token):
        barrier.wait(timeout=10)
        try:
            return identity.service.activate(token)
        except IdentityRejected:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, (token_a, token_b)))
    assert sum(result is not None for result in results) == 1
    assert rows(identity, "invitations")[0]["activated_user_id"] in results
    assert sum(row["consumed_at"] is not None for row in rows(identity, "pending_registrations")) == 1
    assert len(rows(identity, "identity_events")) == 1


@pytest.mark.parametrize("failure", ["expired_invitation", "revoked_invitation", "expired_proof", "wrong_proof"])
def test_verification_failures_preserve_invitation_pending_and_capacity(identity, failure):
    invite, pending_id, token = queued(identity)
    if failure == "revoked_invitation":
        identity.service.revoke_invitation(identity.admin_principal, invite.id)
    elif failure != "wrong_proof":
        name = "invitations" if failure == "expired_invitation" else "pending_registrations"
        with identity.admin.begin() as connection:
            connection.execute(identity_tables[name].update().values(expires_at=sa.func.clock_timestamp() - sa.text("interval '1 second'")))
    before = {name: rows(identity, name) for name in ("invitations", "pending_registrations", "identity_capacity", "identity_audit", "identity_events")}
    with pytest.raises(IdentityRejected):
        identity.service.activate("wrong-synthetic-proof" if failure == "wrong_proof" else token)
    assert {name: rows(identity, name) for name in before} == before


def test_email_bound_invite_mismatch_never_consumes_or_leaks_binding(identity):
    invite = identity.service.create_invitation(identity.admin_principal, kind="email_bound", email="Only.This+local@EXAMPLE.TEST")
    with pytest.raises(IdentityRejected) as caught:
        identity.service.register("other@example.test", PASSWORD, invite.token)
    assert "Only.This+local" not in str(caught.value)
    assert rows(identity, "invitations")[0]["status"] == "open"
    assert rows(identity, "pending_registrations") == []
    _, _, token = queued(identity, "Only.This+local@example.test", invite)
    user_id = identity.service.activate(token)
    with identity.admin.connect() as connection:
        assert connection.scalar(sa.select(tables["users"].c.normalized_email).where(tables["users"].c.id == user_id)) == "Only.This+local@example.test"


def test_proof_is_single_use_and_resend_invalidates_old_link(identity):
    invite, pending, old_token = queued(identity)
    identity.service.resend("synthetic-student@example.test", invite.token)
    with pytest.raises(IdentityRejected):
        identity.service.activate(old_token)
    mail = CapturingMailAdapter()
    assert identity.service.dispatch_mail(mail)
    new_token = mail.messages[-1][1]
    assert new_token != old_token
    user_id = identity.service.activate(new_token)
    with pytest.raises(IdentityRejected):
        identity.service.activate(new_token)
    assert rows(identity, "invitations")[0]["activated_user_id"] == user_id


def test_duplicate_pending_registration_cannot_leave_an_old_valid_proof_after_resend(identity):
    invite = identity.service.create_invitation(identity.admin_principal, kind="single_use")
    _, _, first = queued(identity, invite=invite)
    with pytest.raises(IdentityRejected):
        identity.service.register("synthetic-student@example.test", PASSWORD, invite.token)
    assert len(rows(identity, "pending_registrations")) == 1
    identity.service.resend("synthetic-student@example.test", invite.token)
    with pytest.raises(IdentityRejected):
        identity.service.activate(first)
    capture = CapturingMailAdapter()
    assert identity.service.dispatch_mail(capture)
    assert identity.service.activate(capture.messages[-1][1]) is not None


def test_resend_accepts_frozen_email_only_input_and_invalidates_old_proof(identity):
    _, _, first = queued(identity)
    assert identity.service.resend("synthetic-student@EXAMPLE.TEST") is None
    with pytest.raises(IdentityRejected):
        identity.service.activate(first)
    capture = CapturingMailAdapter()
    assert identity.service.dispatch_mail(capture)
    assert identity.service.activate(capture.messages[-1][1]) is not None


def test_two_mail_dispatchers_compete_for_one_durable_intent(identity):
    invite = identity.service.create_invitation(identity.admin_principal, kind="single_use")
    identity.service.register("synthetic-mail-race@example.test", PASSWORD, invite.token)
    barrier = Barrier(2)
    capture = CapturingMailAdapter()
    def compete(_):
        barrier.wait(timeout=10)
        return identity.service.dispatch_mail(capture)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, range(2)))
    assert sorted(results) == [False, True]
    assert len(capture.messages) == 1
    assert rows(identity, "identity_mail_intents")[0]["state"] == "sent"
    assert identity.service.activate(capture.messages[0][1]) is not None


def test_expired_first_mail_intent_does_not_block_the_next_live_intent(identity):
    expired_invite = identity.service.create_invitation(identity.admin_principal, kind="single_use")
    expired_id = identity.service.register("synthetic-expired-mail@example.test", PASSWORD, expired_invite.token)
    live_invite = identity.service.create_invitation(identity.admin_principal, kind="single_use")
    identity.service.register("synthetic-live-mail@example.test", PASSWORD, live_invite.token)
    with identity.admin.begin() as connection:
        connection.execute(identity_tables["pending_registrations"].update().where(
            identity_tables["pending_registrations"].c.id == expired_id).values(
            expires_at=sa.func.clock_timestamp() - sa.text("interval '1 second'")))
    capture = CapturingMailAdapter()
    assert identity.service.dispatch_mail(capture)
    assert [email for email, _ in capture.messages] == ["synthetic-live-mail@example.test"]
    assert {row["state"] for row in rows(identity, "identity_mail_intents")} == {"cancelled", "sent"}
    assert identity.service.dispatch_mail(capture) is False


def test_different_invites_racing_same_email_activate_one_user_and_keep_losing_invite(identity):
    _, _, first = queued(identity)
    _, _, second = queued(identity)
    barrier = Barrier(2)
    def compete(token):
        barrier.wait(timeout=10)
        try:
            return identity.service.activate(token)
        except IdentityRejected:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, (first, second)))
    assert sum(value is not None for value in results) == 1
    with identity.admin.connect() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(tables["users"]).where(
            tables["users"].c.normalized_email == "synthetic-student@example.test")) == 1
    assert {row["status"] for row in rows(identity, "invitations")} == {"open", "consumed"}
    assert sum(row["consumed_at"] is not None for row in rows(identity, "pending_registrations")) == 1
    assert rows(identity, "identity_capacity")[0]["activated_count"] == 2
    assert len(rows(identity, "identity_events")) == 1


def test_durable_mail_intent_recovers_after_uncertain_delivery_without_storing_token(identity):
    invite = identity.service.create_invitation(identity.admin_principal, kind="single_use")
    identity.service.register("synthetic-mail-recovery@example.test", PASSWORD, invite.token)
    uncertain = []
    class FailedDelivery:
        def send_verification(self, email, token):
            uncertain.append((email, token))
            raise RuntimeError("synthetic delivery outcome unknown")
    with pytest.raises(RuntimeError, match="synthetic delivery outcome unknown"):
        identity.service.dispatch_mail(FailedDelivery())
    intent = rows(identity, "identity_mail_intents")[0]
    assert intent["state"] == "sending" and intent["lease_until"] is not None
    assert uncertain[0][1] not in repr(intent)
    with identity.admin.begin() as connection:
        connection.execute(identity_tables["identity_mail_intents"].update().values(
            lease_until=sa.func.clock_timestamp() - sa.text("interval '1 second'")))
    recovered = CapturingMailAdapter()
    assert identity.service.dispatch_mail(recovered)
    assert recovered.messages[0][1] != uncertain[0][1]
    with pytest.raises(IdentityRejected):
        identity.service.activate(uncertain[0][1])
    identity.service.resend("synthetic-mail-recovery@example.test", invite.token)
    with pytest.raises(IdentityRejected):
        identity.service.activate(recovered.messages[0][1])
    final = CapturingMailAdapter()
    assert identity.service.dispatch_mail(final)
    assert identity.service.activate(final.messages[0][1]) is not None


def test_activation_error_after_writes_rolls_everything_back_and_proof_retries(identity):
    _, _, token = queued(identity)
    before = {name: rows(identity, name) for name in ("invitations", "pending_registrations", "identity_capacity", "identity_audit", "identity_events")}
    with identity.admin.begin() as connection:
        connection.exec_driver_sql("CREATE FUNCTION pr03a_injected_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic activation failure' USING ERRCODE='23514'; END $$")
        connection.exec_driver_sql("CREATE TRIGGER pr03a_activation_failure BEFORE INSERT ON identity_events FOR EACH ROW EXECUTE FUNCTION pr03a_injected_failure()")
    with pytest.raises((sa.exc.DBAPIError, IdentityRejected)):
        identity.service.activate(token)
    assert {name: rows(identity, name) for name in before} == before
    with identity.admin.begin() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(tables["users"])) == 1
        connection.exec_driver_sql("DROP TRIGGER pr03a_activation_failure ON identity_events")
        connection.exec_driver_sql("DROP FUNCTION pr03a_injected_failure()")
    assert identity.service.activate(token) is not None


@pytest.mark.parametrize("capacity", [10, 30])
def test_capacity_last_seat_race_never_consumes_losing_invite(identity, capacity):
    identity.service.set_capacity(identity.admin_principal, capacity)
    with identity.admin.begin() as connection:
        connection.execute(tables["users"].insert(), [{"id": uuid4(), "role": "student", "state": "active", "normalized_email": f"synthetic-seat-{i}@example.test", "email_verified_at": datetime.now(timezone.utc)} for i in range(capacity - 2)])
    first, _, token_a = queued(identity, "synthetic-final-a@example.test")
    second, _, token_b = queued(identity, "synthetic-final-b@example.test")
    barrier = Barrier(2)
    def compete(token):
        barrier.wait(timeout=10)
        try:
            return identity.service.activate(token)
        except IdentityRejected:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, (token_a, token_b)))
    assert sum(result is not None for result in results) == 1
    with identity.admin.connect() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(tables["users"]).where(tables["users"].c.state == "active")) == capacity
    invites = rows(identity, "invitations")
    assert {row["status"] for row in invites} == {"open", "consumed"}
    assert sum(row["consumed_at"] is not None for row in rows(identity, "pending_registrations")) == 1
    assert rows(identity, "identity_capacity")[0]["activated_count"] <= capacity
    loser_token = token_a if results[0] is None else token_b
    before = {name: rows(identity, name) for name in ("invitations", "pending_registrations", "identity_capacity")}
    with pytest.raises(IdentityRejected):
        identity.service.activate(loser_token)
    assert {name: rows(identity, name) for name in before} == before


def test_invitation_always_activates_student_and_student_cannot_manage(identity):
    student, invite, _, _ = activated(identity)
    assert student.role == "student"
    with identity.admin.connect() as connection:
        assert connection.scalar(sa.select(tables["users"].c.role).where(tables["users"].c.id == student.user_id)) == "student"
    with pytest.raises(AuthorizationDenied):
        identity.service.create_invitation(student, kind="single_use")
    with pytest.raises(AuthorizationDenied):
        identity.service.revoke_invitation(student, invite.id)
    with pytest.raises(AuthorizationDenied):
        identity.service.set_capacity(student, 30)
    with pytest.raises(TypeError):
        identity.service.create_invitation(identity.admin_principal, kind="single_use", role="admin")


@pytest.mark.parametrize("change", ["mfa", "reauth", "expired", "epoch", "disabled", "role"])
def test_admin_management_rechecks_all_trusted_auth_evidence(identity, change):
    admin = identity.admin_principal
    if change == "mfa":
        admin = replace(admin, mfa_level="none")
    elif change == "reauth":
        admin = replace(admin, reauthenticated_at=datetime.now(timezone.utc) - timedelta(hours=1))
    elif change == "expired":
        admin = replace(admin, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    elif change == "epoch":
        admin = replace(admin, security_epoch=admin.security_epoch + 1)
    elif change == "role":
        admin = replace(admin, role="student")
    else:
        with identity.admin.begin() as connection:
            connection.execute(tables["users"].update().values(state="disabled"))
    with pytest.raises(AuthorizationDenied):
        identity.service.create_invitation(admin, kind="single_use")
    assert rows(identity, "invitations") == []


def test_authorized_owner_boundary_denies_admin_foreign_resources_and_sql_cross_links(identity):
    student, _, _, _ = activated(identity)
    admin = identity.admin_principal
    with identity.runtime.authorized_transaction(student) as tx:
        semester = tx.catalog.create_semester("Synthetic private semester")
        course = tx.catalog.create_course(semester.id, "Synthetic private course")
        classroom = tx.catalog.create_class_session(course.id, "Synthetic private class")
        task = tx.tasks.enqueue("export", {"schema_version": 1}, op_id=uuid4(), class_session_id=classroom.id)
    with identity.runtime.authorized_transaction(admin) as tx:
        for read, resource_id in ((tx.catalog.get_semester, semester.id), (tx.catalog.get_course, course.id), (tx.catalog.get_class_session, classroom.id), (tx.catalog.get_material, course.current_material_version_id), (tx.tasks.get, task.id)):
            with pytest.raises(NotFound):
                read(resource_id)
        with pytest.raises(NotFound):
            tx.catalog.rename_course(course.id, 1, name="forbidden")
        admin_semester = tx.catalog.create_semester("Synthetic admin-owned semester")
        assert tx.connection.execute(sa.select(tables["courses"])).all() == []
    with pytest.raises(sa.exc.DBAPIError):
        with identity.runtime.authorized_transaction(student) as tx:
            tx.connection.execute(tables["courses"].update().where(tables["courses"].c.id == course.id).values(semester_id=admin_semester.id, revision=2))
    with identity.runtime.authorized_transaction(student) as tx:
        assert tx.catalog.get_course(course.id).semester_id == semester.id
        with pytest.raises(TypeError):
            tx.catalog.create_semester("cannot spoof", owner_id=admin.user_id)
    with pytest.raises((TypeError, AuthorizationDenied)):
        with identity.runtime.authorized_transaction(OwnerContext(student.user_id)):
            pytest.fail("bare owner UUID accepted as authenticated principal")


@pytest.mark.parametrize("change", ["disabled", "deleted", "unverified", "password_missing", "role_changed", "stale_epoch", "expired", "missing"])
def test_owner_entrypoint_rejects_inactive_or_invalid_subject(identity, change):
    user, _, _, _ = activated(identity)
    if change in ("disabled", "deleted", "unverified", "password_missing", "role_changed"):
        values = {"disabled": {"state": "disabled"},
                  "deleted": {"deleted_at": datetime.now(timezone.utc)},
                  "unverified": {"email_verified_at": None},
                  "password_missing": {"password_hash": None},
                  "role_changed": {"role": "admin"}}[change]
        with identity.admin.begin() as connection:
            connection.execute(tables["users"].update().where(tables["users"].c.id == user.user_id).values(**values))
    elif change == "stale_epoch":
        user = replace(user, security_epoch=user.security_epoch + 1)
    elif change == "expired":
        user = replace(user, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    else:
        user = replace(user, user_id=uuid4())
    with pytest.raises(AuthorizationDenied):
        with identity.runtime.authorized_transaction(user):
            pytest.fail("invalid subject entered owner transaction")


def test_rls_and_internal_owner_uow_reject_lost_email_verification(identity):
    user, _, _, _ = activated(identity)
    with identity.runtime.authorized_transaction(user) as tx:
        semester = tx.catalog.create_semester("Synthetic verified-owner semester")
        tx.catalog.create_course(semester.id, "Synthetic private course")
    with identity.admin.begin() as connection:
        connection.execute(tables["users"].update().where(tables["users"].c.id == user.user_id).values(
            email_verified_at=None))
    with identity.runtime.engine.begin() as connection:
        connection.execute(sa.text("SELECT set_config('app.owner_id',:owner,true)"), {"owner": str(user.user_id)})
        assert connection.execute(sa.select(tables["courses"])).all() == []
    with pytest.raises(InactiveOwner):
        with identity.runtime.transaction(OwnerContext(user.user_id)):
            pytest.fail("unverified account entered internal owner UoW")


def test_identity_and_learning_database_roles_cannot_read_each_others_data(identity):
    with identity.runtime.engine.connect() as connection:
        role = connection.execute(sa.text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")).one()
        assert not role.rolsuper and not role.rolbypassrls
    for name in identity_tables:
        with identity.admin.connect() as connection:
            flags = connection.execute(sa.text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=to_regclass(:name)"), {"name": name}).one()
            assert flags.relrowsecurity and flags.relforcerowsecurity
        with pytest.raises(sa.exc.DBAPIError):
            with identity.runtime.engine.begin() as connection:
                connection.execute(sa.select(identity_tables[name]))
    identity_engine = sa.create_engine(identity.iurl, connect_args=synthetic_connect_args())
    try:
        with pytest.raises(sa.exc.DBAPIError):
            with identity_engine.begin() as connection:
                connection.execute(sa.select(tables["courses"]))
    finally:
        identity_engine.dispose()
