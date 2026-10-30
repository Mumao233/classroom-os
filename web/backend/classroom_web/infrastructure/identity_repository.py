"""Restricted identity control plane; all admission mutations commit together."""
from contextlib import contextmanager
from datetime import timedelta
import hashlib
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.engine import make_url

from ..domain.identity import AuthorizationDenied, IdentityRejected, Principal
from .identity_schema import tables
from .schema import users


class PgIdentityRepository:
    def __init__(self, url):
        if make_url(url).drivername != "postgresql+psycopg":
            raise ValueError("an explicit postgresql+psycopg URL is required")
        self.engine = sa.create_engine(url, pool_size=4, max_overflow=0,
                                      pool_pre_ping=True, hide_parameters=True,
                                      isolation_level="READ COMMITTED")

    def dispose(self):
        self.engine.dispose()

    @contextmanager
    def _transaction(self):
        try:
            with self.engine.begin() as connection:
                safe = connection.scalar(sa.text("""
                    SELECT NOT (r.rolsuper OR r.rolbypassrls OR r.rolcreaterole OR r.rolcreatedb)
                      AND pg_has_role(current_user,'classroom_web_identity','USAGE')
                      AND NOT pg_has_role(current_user,'classroom_web_runtime','USAGE')
                      AND NOT EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                        WHERE n.nspname='public' AND c.relkind IN ('r','p')
                          AND pg_has_role(current_user,c.relowner,'USAGE'))
                    FROM pg_roles r WHERE r.rolname=current_user
                """))
                if safe is not True:
                    raise PermissionError("identity requires a separate restricted control-plane login")
                connection.execute(sa.text("SET LOCAL search_path=public,pg_temp"))
                connection.execute(sa.text("SET LOCAL timezone='UTC'"))
                connection.execute(sa.text("SET LOCAL statement_timeout='15s'"))
                connection.execute(sa.text("SET LOCAL lock_timeout='5s'"))
                yield connection
        except sa.exc.IntegrityError:
            raise IdentityRejected() from None

    @staticmethod
    def _now(connection):
        return connection.scalar(sa.select(sa.func.clock_timestamp()))

    def _admin(self, connection, principal, window):
        if not isinstance(principal, Principal) or principal.role != "admin" or principal.mfa_level != "totp":
            raise AuthorizationDenied()
        if not connection.scalar(sa.text("SELECT classroom_web_identity_admin_valid(:uid,:epoch)"),
                                 {"uid": principal.user_id, "epoch": principal.security_epoch}):
            raise AuthorizationDenied()
        now = self._now(connection)
        if principal.expires_at <= now or principal.reauthenticated_at is None or not (
                now - window <= principal.reauthenticated_at <= now):
            raise AuthorizationDenied()

    @staticmethod
    def _audit(connection, action, *, actor=None, subject=None, target=None):
        connection.execute(tables["identity_audit"].insert().values(
            id=uuid4(), action=action, actor_id=actor, subject_user_id=subject, target_id=target))

    def _control(self, connection):
        control = tables["identity_capacity"]
        return connection.execute(sa.select(control).where(control.c.id == 1).with_for_update()).mappings().one()

    def _admitted(self, connection, control):
        active = connection.scalar(sa.select(sa.func.count()).select_from(users).where(
            users.c.state == "active", users.c.deleted_at.is_(None)))
        # No disabling/re-enrollment policy is introduced here. Reuse requires
        # an independently reviewed lifecycle policy; admission stays bounded.
        return max(control["activated_count"], active)

    def create_invitation(self, principal, binding, digest, window):
        with self._transaction() as connection:
            self._admin(connection, principal, window)
            uid, expires = uuid4(), self._now(connection) + timedelta(days=7)
            connection.execute(tables["invitations"].insert().values(id=uid, token_hash=digest,
                email_binding=binding, status="open", expires_at=expires, created_by=principal.user_id))
            self._audit(connection, "invitation_created", actor=principal.user_id, target=uid)
            return uid, expires

    def revoke_invitation(self, principal, invitation_id, window):
        invite = tables["invitations"]
        with self._transaction() as connection:
            self._admin(connection, principal, window)
            row = connection.execute(sa.select(invite).where(invite.c.id == invitation_id)
                                     .with_for_update()).mappings().one_or_none()
            if row is None or row["status"] == "consumed":
                raise IdentityRejected("INVITATION_UNAVAILABLE")
            if row["status"] != "revoked":
                connection.execute(invite.update().where(invite.c.id == invitation_id).values(
                    status="revoked", revision=invite.c.revision+1, updated_at=self._now(connection)))
                self._audit(connection, "invitation_revoked", actor=principal.user_id, target=invitation_id)

    def set_capacity(self, principal, capacity, window):
        with self._transaction() as connection:
            control = self._control(connection)
            self._admin(connection, principal, window)
            count = self._admitted(connection, control)
            if count > capacity:
                raise IdentityRejected("CAPACITY_FULL")
            table = tables["identity_capacity"]
            connection.execute(table.update().where(table.c.id == 1).values(max_active_users=capacity,
                activated_count=count, revision=table.c.revision+1, updated_at=self._now(connection)))
            self._audit(connection, "capacity_changed", actor=principal.user_id)

    def _invite(self, connection, *, digest=None, uid=None):
        table = tables["invitations"]
        predicate = table.c.token_hash == digest if digest is not None else table.c.id == uid
        return connection.execute(sa.select(table).where(predicate).with_for_update()).mappings().one_or_none()

    @staticmethod
    def _check_invite(invite, email, now):
        if invite is None or invite["status"] != "open" or invite["expires_at"] <= now or (
                invite["email_binding"] is not None and invite["email_binding"] != email):
            raise IdentityRejected("INVITATION_UNAVAILABLE")

    def register(self, email, password_hash, invite_digest, proof_digest, profile):
        pending, mail = tables["pending_registrations"], tables["identity_mail_intents"]
        with self._transaction() as connection:
            control = self._control(connection)
            if not control["registration_enabled"]:
                raise IdentityRejected()
            invite = self._invite(connection, digest=invite_digest)
            now = self._now(connection)
            self._check_invite(invite, email, now)
            # Existing-account errors share the ordinary rejection shape.
            if connection.scalar(sa.select(users.c.id).where(users.c.normalized_email == email)):
                raise IdentityRejected()
            if connection.scalar(sa.select(pending.c.id).where(pending.c.normalized_email == email,
                    pending.c.invitation_id == invite["id"], pending.c.consumed_at.is_(None),
                    pending.c.expires_at > now).limit(1)):
                raise IdentityRejected()
            uid = uuid4()
            connection.execute(pending.insert().values(id=uid, normalized_email=email,
                password_hash=password_hash, invitation_id=invite["id"], expires_at=now+timedelta(minutes=30),
                email_proof_hash=proof_digest, profile=profile))
            connection.execute(mail.insert().values(id=uuid4(), pending_registration_id=uid, generation=1))
            self._audit(connection, "registration_pending", target=uid)
            return uid

    def resend(self, email, invite_digest, invalid_digest, interval):
        pending, mail = tables["pending_registrations"], tables["identity_mail_intents"]
        with self._transaction() as connection:
            self._control(connection)
            if invite_digest is None:
                invitations = tables["invitations"]
                hint = connection.execute(sa.select(pending.c.invitation_id).join(invitations,
                    invitations.c.id == pending.c.invitation_id).where(
                    pending.c.normalized_email == email, pending.c.consumed_at.is_(None),
                    invitations.c.status == "open", invitations.c.expires_at > sa.func.clock_timestamp())
                    .order_by(pending.c.created_at.desc(), pending.c.id).limit(1)).one_or_none()
                if hint is None:
                    raise IdentityRejected()
                invite = self._invite(connection, uid=hint.invitation_id)
            else:
                invite = self._invite(connection, digest=invite_digest)
            self._check_invite(invite, email, self._now(connection))
            matches = connection.execute(sa.select(pending).where(pending.c.normalized_email == email,
                pending.c.consumed_at.is_(None)).order_by(pending.c.id).with_for_update()).mappings().all()
            selected = [row for row in matches if row["invitation_id"] == invite["id"]]
            if not selected:
                raise IdentityRejected()
            row = max(selected, key=lambda item: (item["created_at"], item["id"]))
            last = connection.execute(sa.select(mail).where(mail.c.pending_registration_id == row["id"])
                                      .order_by(mail.c.generation.desc()).limit(1).with_for_update()).mappings().one()
            now = self._now(connection)
            if last["created_at"] + interval > now:
                raise IdentityRejected("RATE_LIMITED")
            for prior in matches:
                connection.execute(mail.update().where(mail.c.pending_registration_id == prior["id"]).values(
                    state="cancelled", sent_at=None, lease_until=None))
                connection.execute(pending.update().where(pending.c.id == prior["id"]).values(
                    email_proof_hash=hashlib.sha256(invalid_digest+prior["id"].bytes).digest()))
            connection.execute(pending.update().where(pending.c.id == row["id"]).values(expires_at=now+timedelta(minutes=30)))
            connection.execute(mail.insert().values(id=uuid4(), pending_registration_id=row["id"],
                                                     generation=last["generation"]+1))
            self._audit(connection, "verification_resent", target=row["id"])

    def claim_mail(self, proof_digest):
        for _ in range(100):
            claim = self._claim_mail_once(proof_digest)
            if claim is not False:
                return claim
        return None

    def _claim_mail_once(self, proof_digest):
        pending, mail = tables["pending_registrations"], tables["identity_mail_intents"]
        with self._transaction() as connection:
            eligible = sa.or_(mail.c.state == "queued", sa.and_(mail.c.state == "sending",
                                                               mail.c.lease_until <= sa.func.clock_timestamp()))
            # Pending first is shared with activation/resend. SKIP LOCKED gives
            # independent workers exclusive claims without holding locks at send.
            row = connection.execute(sa.select(pending).join(mail,
                mail.c.pending_registration_id == pending.c.id).where(eligible)
                .order_by(mail.c.created_at, mail.c.id).limit(1)
                .with_for_update(of=pending, skip_locked=True)).mappings().one_or_none()
            if row is None:
                return None
            intent = connection.execute(sa.select(mail).where(mail.c.pending_registration_id == row["id"], eligible)
                .order_by(mail.c.generation.desc()).limit(1).with_for_update()).mappings().one_or_none()
            if intent is None:
                return None
            now = self._now(connection)
            if row["consumed_at"] is not None or row["expires_at"] <= now:
                connection.execute(mail.update().where(mail.c.id == intent["id"]).values(
                    state="cancelled", sent_at=None, lease_until=None))
                return False
            connection.execute(pending.update().where(pending.c.id == row["id"]).values(
                email_proof_hash=proof_digest, expires_at=now+timedelta(minutes=30)))
            lease = now+timedelta(minutes=1)
            connection.execute(mail.update().where(mail.c.id == intent["id"]).values(state="sending", lease_until=lease))
            return {"id": intent["id"], "pending_id": row["id"], "normalized_email": row["normalized_email"],
                    "lease_until": lease}

    def complete_mail(self, claim, proof_digest):
        pending, mail = tables["pending_registrations"], tables["identity_mail_intents"]
        with self._transaction() as connection:
            row = connection.execute(sa.select(pending.c.email_proof_hash).where(pending.c.id == claim["pending_id"])
                                     .with_for_update()).one()
            if row.email_proof_hash != proof_digest:
                return
            connection.execute(mail.update().where(mail.c.id == claim["id"], mail.c.state == "sending",
                mail.c.lease_until == claim["lease_until"]).values(state="sent", sent_at=self._now(connection), lease_until=None))

    def activate(self, proof_digest):
        pending, invite = tables["pending_registrations"], tables["invitations"]
        with self._transaction() as connection:
            control = self._control(connection)
            hint = connection.execute(sa.select(pending.c.id, pending.c.invitation_id).where(
                pending.c.email_proof_hash == proof_digest)).mappings().one_or_none()
            if hint is None or not control["registration_enabled"]:
                raise IdentityRejected("VERIFICATION_INVALID")
            invitation = self._invite(connection, uid=hint["invitation_id"])
            row = connection.execute(sa.select(pending).where(pending.c.id == hint["id"])
                                     .with_for_update()).mappings().one()
            now = self._now(connection)
            if row["email_proof_hash"] != proof_digest or row["consumed_at"] is not None or row["expires_at"] <= now:
                raise IdentityRejected("VERIFICATION_INVALID")
            self._check_invite(invitation, row["normalized_email"], now)
            count = self._admitted(connection, control)
            if count >= control["max_active_users"]:
                raise IdentityRejected("CAPACITY_FULL")
            user_id = uuid4()
            connection.execute(users.insert().values(id=user_id, normalized_email=row["normalized_email"],
                password_hash=row["password_hash"], role="student", state="active", email_verified_at=now,
                profile=row["profile"]))
            connection.execute(invite.update().where(invite.c.id == invitation["id"]).values(
                status="consumed", activated_user_id=user_id, revision=invite.c.revision+1, updated_at=now))
            connection.execute(pending.update().where(pending.c.id == row["id"]).values(consumed_at=now))
            capacity = tables["identity_capacity"]
            connection.execute(capacity.update().where(capacity.c.id == 1).values(activated_count=count+1,
                revision=capacity.c.revision+1, updated_at=now))
            self._audit(connection, "account_activated", subject=user_id, target=invitation["id"])
            connection.execute(tables["identity_events"].insert().values(id=uuid4(), user_id=user_id, kind="account_activated"))
            return user_id
