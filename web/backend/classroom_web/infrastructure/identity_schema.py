"""PR-03a identity metadata; internal control-plane records are not wire DTOs.

The invitation and pending columns follow the frozen logical contract. Narrow
internal capacity, mail-intent and audit/event records fill implementation gaps
without adding HTTP fields or WebSocket event types. No bearer secret is stored
in a mail intent, audit record or event. Alembic uses its own frozen SQL snapshot.
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .schema import metadata, _base


invitations = sa.Table("invitations", metadata, *_base(),
    sa.Column("token_hash", sa.LargeBinary, nullable=False),
    sa.Column("email_binding", sa.Text),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("activated_user_id", UUID(as_uuid=True)),
    sa.Column("created_by", UUID(as_uuid=True), nullable=False),
    sa.Column("revision", sa.BigInteger, nullable=False, server_default="1"),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
              server_default=sa.text("transaction_timestamp()")),
    sa.UniqueConstraint("token_hash"),
    sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["activated_user_id"], ["users.id"], ondelete="RESTRICT"),
    sa.CheckConstraint("octet_length(token_hash) = 32", name="token_hash"),
    sa.CheckConstraint("email_binding IS NULL OR char_length(email_binding) BETWEEN 3 AND 254",
                       name="email_binding"),
    sa.CheckConstraint("status IN ('open','consumed','revoked','expired')", name="status"),
    sa.CheckConstraint("(status = 'consumed') = (activated_user_id IS NOT NULL)",
                       name="consumption"),
    sa.CheckConstraint("revision >= 1", name="revision_positive"),
)

pending_registrations = sa.Table("pending_registrations", metadata, *_base(),
    sa.Column("normalized_email", sa.Text, nullable=False),
    sa.Column("password_hash", sa.Text, nullable=False),
    sa.Column("invitation_id", UUID(as_uuid=True), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("email_proof_hash", sa.LargeBinary, nullable=False),
    sa.Column("consumed_at", sa.DateTime(timezone=True)),
    sa.Column("profile", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.UniqueConstraint("email_proof_hash"),
    sa.ForeignKeyConstraint(["invitation_id"], ["invitations.id"], ondelete="RESTRICT"),
    sa.CheckConstraint("char_length(normalized_email) BETWEEN 3 AND 254", name="email"),
    sa.CheckConstraint("password_hash LIKE '$argon2id$%' AND char_length(password_hash) <= 1024",
                       name="password_hash"),
    sa.CheckConstraint("octet_length(email_proof_hash) = 32", name="email_proof_hash"),
    sa.CheckConstraint("jsonb_typeof(profile) = 'object' AND pg_column_size(profile) <= 1024",
                       name="bounded_profile"),
)

identity_capacity = sa.Table("identity_capacity", metadata,
    sa.Column("id", sa.SmallInteger, primary_key=True, autoincrement=False),
    sa.Column("max_active_users", sa.Integer, nullable=False, server_default="10"),
    sa.Column("activated_count", sa.BigInteger, nullable=False, server_default="0"),
    sa.Column("registration_enabled", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("revision", sa.BigInteger, nullable=False, server_default="1"),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
              server_default=sa.text("transaction_timestamp()")),
    sa.CheckConstraint("id = 1", name="singleton"),
    sa.CheckConstraint("max_active_users IN (10,30)", name="stage"),
    sa.CheckConstraint("activated_count BETWEEN 0 AND max_active_users", name="count"),
    sa.CheckConstraint("revision >= 1", name="revision_positive"),
)

identity_mail_intents = sa.Table("identity_mail_intents", metadata, *_base(),
    sa.Column("pending_registration_id", UUID(as_uuid=True), nullable=False),
    sa.Column("generation", sa.BigInteger, nullable=False, server_default="1"),
    sa.Column("state", sa.Text, nullable=False, server_default="queued"),
    sa.Column("sent_at", sa.DateTime(timezone=True)),
    sa.Column("lease_until", sa.DateTime(timezone=True)),
    sa.UniqueConstraint("pending_registration_id", "generation", name="uq_identity_mail_generation"),
    sa.ForeignKeyConstraint(["pending_registration_id"], ["pending_registrations.id"],
                            ondelete="RESTRICT"),
    sa.CheckConstraint("generation >= 1", name="generation"),
    sa.CheckConstraint("state IN ('queued','sending','sent','cancelled')", name="state"),
    sa.CheckConstraint("(state = 'sent') = (sent_at IS NOT NULL)", name="sent_at"),
    sa.CheckConstraint("(state = 'sending') = (lease_until IS NOT NULL)", name="lease"),
)

identity_audit = sa.Table("identity_audit", metadata, *_base(),
    sa.Column("actor_id", UUID(as_uuid=True)),
    sa.Column("subject_user_id", UUID(as_uuid=True)),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("target_id", UUID(as_uuid=True)),
    sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
    sa.ForeignKeyConstraint(["subject_user_id"], ["users.id"], ondelete="RESTRICT"),
    sa.CheckConstraint("action IN ('invitation_created','invitation_revoked',"
                       "'registration_pending','verification_resent','account_activated',"
                       "'capacity_changed')", name="action"),
)

identity_events = sa.Table("identity_events", metadata, *_base(),
    sa.Column("user_id", UUID(as_uuid=True), nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("published_at", sa.DateTime(timezone=True)),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
    sa.UniqueConstraint("user_id", "kind", name="uq_identity_activation_event"),
    sa.CheckConstraint("kind = 'account_activated'", name="kind"),
)

sa.Index("ix_pending_email", pending_registrations.c.normalized_email)
sa.Index("ix_pending_invitation", pending_registrations.c.invitation_id)
sa.Index("ix_identity_mail_state", identity_mail_intents.c.state, identity_mail_intents.c.created_at)
sa.Index("ix_identity_audit_created", identity_audit.c.created_at, identity_audit.c.action)

tables = {table.name: table for table in (invitations, pending_registrations,
          identity_capacity, identity_mail_intents, identity_audit, identity_events)}
