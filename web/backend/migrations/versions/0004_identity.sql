-- Frozen PR-03a identity snapshot. Apply only through Alembic.

CREATE TABLE invitations (
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	token_hash BYTEA NOT NULL,
	email_binding TEXT,
	status TEXT NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	activated_user_id UUID,
	created_by UUID NOT NULL,
	revision BIGINT DEFAULT '1' NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	CONSTRAINT pk_invitations PRIMARY KEY (id),
	CONSTRAINT uq_invitations_token_hash UNIQUE (token_hash),
	CONSTRAINT fk_invitations_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT fk_invitations_activated_user_id_users FOREIGN KEY(activated_user_id) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT ck_invitations_token_hash CHECK (octet_length(token_hash) = 32),
	CONSTRAINT ck_invitations_email_binding CHECK (email_binding IS NULL OR char_length(email_binding) BETWEEN 3 AND 254),
	CONSTRAINT ck_invitations_status CHECK (status IN ('open','consumed','revoked','expired')),
	CONSTRAINT ck_invitations_consumption CHECK ((status = 'consumed') = (activated_user_id IS NOT NULL)),
	CONSTRAINT ck_invitations_revision_positive CHECK (revision >= 1)
);

CREATE TABLE pending_registrations (
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	normalized_email TEXT NOT NULL,
	password_hash TEXT NOT NULL,
	invitation_id UUID NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	email_proof_hash BYTEA NOT NULL,
	consumed_at TIMESTAMP WITH TIME ZONE,
	profile JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_pending_registrations PRIMARY KEY (id),
	CONSTRAINT uq_pending_registrations_email_proof_hash UNIQUE (email_proof_hash),
	CONSTRAINT fk_pending_registrations_invitation_id_invitations FOREIGN KEY(invitation_id) REFERENCES invitations (id) ON DELETE RESTRICT,
	CONSTRAINT ck_pending_registrations_email CHECK (char_length(normalized_email) BETWEEN 3 AND 254),
	CONSTRAINT ck_pending_registrations_password_hash CHECK (password_hash LIKE '$argon2id$%' AND char_length(password_hash) <= 1024),
	CONSTRAINT ck_pending_registrations_email_proof_hash CHECK (octet_length(email_proof_hash) = 32),
	CONSTRAINT ck_pending_registrations_bounded_profile CHECK (jsonb_typeof(profile) = 'object' AND pg_column_size(profile) <= 1024)
);

CREATE INDEX ix_pending_email ON pending_registrations (normalized_email);

CREATE INDEX ix_pending_invitation ON pending_registrations (invitation_id);

CREATE TABLE identity_capacity (
	id SMALLINT NOT NULL,
	max_active_users INTEGER DEFAULT '10' NOT NULL,
	activated_count BIGINT DEFAULT '0' NOT NULL,
	registration_enabled BOOLEAN DEFAULT true NOT NULL,
	revision BIGINT DEFAULT '1' NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	CONSTRAINT pk_identity_capacity PRIMARY KEY (id),
	CONSTRAINT ck_identity_capacity_singleton CHECK (id = 1),
	CONSTRAINT ck_identity_capacity_stage CHECK (max_active_users IN (10,30)),
	CONSTRAINT ck_identity_capacity_count CHECK (activated_count BETWEEN 0 AND max_active_users),
	CONSTRAINT ck_identity_capacity_revision_positive CHECK (revision >= 1)
);

CREATE TABLE identity_mail_intents (
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	pending_registration_id UUID NOT NULL,
	generation BIGINT DEFAULT '1' NOT NULL,
	state TEXT DEFAULT 'queued' NOT NULL,
	sent_at TIMESTAMP WITH TIME ZONE,
	lease_until TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_identity_mail_intents PRIMARY KEY (id),
	CONSTRAINT uq_identity_mail_generation UNIQUE (pending_registration_id, generation),
	CONSTRAINT fk_identity_mail_intents_pending_registration_id_pendin_4eb3 FOREIGN KEY(pending_registration_id) REFERENCES pending_registrations (id) ON DELETE RESTRICT,
	CONSTRAINT ck_identity_mail_intents_generation CHECK (generation >= 1),
	CONSTRAINT ck_identity_mail_intents_state CHECK (state IN ('queued','sending','sent','cancelled')),
	CONSTRAINT ck_identity_mail_intents_sent_at CHECK ((state = 'sent') = (sent_at IS NOT NULL)),
	CONSTRAINT ck_identity_mail_intents_lease CHECK ((state = 'sending') = (lease_until IS NOT NULL))
);

CREATE INDEX ix_identity_mail_state ON identity_mail_intents (state, created_at);

CREATE TABLE identity_audit (
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	actor_id UUID,
	subject_user_id UUID,
	action TEXT NOT NULL,
	target_id UUID,
	CONSTRAINT pk_identity_audit PRIMARY KEY (id),
	CONSTRAINT fk_identity_audit_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT fk_identity_audit_subject_user_id_users FOREIGN KEY(subject_user_id) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT ck_identity_audit_action CHECK (action IN ('invitation_created','invitation_revoked','registration_pending','verification_resent','account_activated','capacity_changed'))
);

CREATE INDEX ix_identity_audit_created ON identity_audit (created_at, action);

CREATE TABLE identity_events (
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	user_id UUID NOT NULL,
	kind TEXT NOT NULL,
	published_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_identity_events PRIMARY KEY (id),
	CONSTRAINT fk_identity_events_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT uq_identity_activation_event UNIQUE (user_id, kind),
	CONSTRAINT ck_identity_events_kind CHECK (kind = 'account_activated')
);

-- A separate service identity operates only these control-plane tables. It is
-- never the API/worker runtime and cannot select student learning content.
DO $role$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'classroom_web_identity') THEN
    CREATE ROLE classroom_web_identity NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
      NOREPLICATION NOBYPASSRLS;
  ELSIF EXISTS (SELECT FROM pg_roles WHERE rolname = 'classroom_web_identity'
      AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolcanlogin OR rolreplication)) THEN
    RAISE EXCEPTION 'classroom_web_identity must be an unprivileged NOLOGIN group';
  END IF;
  IF EXISTS (SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
      WHERE r.rolname = 'classroom_web_identity') THEN
    RAISE EXCEPTION 'classroom_web_identity must not inherit other roles';
  END IF;
  IF pg_has_role('classroom_web_runtime', 'classroom_web_identity', 'MEMBER')
      OR pg_has_role('classroom_web_task_dispatcher', 'classroom_web_identity', 'MEMBER')
      OR pg_has_role('classroom_web_scheduler', 'classroom_web_identity', 'MEMBER') THEN
    RAISE EXCEPTION 'application and worker groups must not inherit identity authority';
  END IF;
END
$role$;

REVOKE ALL ON TABLE invitations, pending_registrations, identity_capacity,
  identity_mail_intents, identity_audit, identity_events FROM PUBLIC,
  classroom_web_runtime, classroom_web_scheduler, classroom_web_task_dispatcher;
GRANT USAGE ON SCHEMA public TO classroom_web_identity;
REVOKE CREATE ON SCHEMA public FROM classroom_web_identity;
GRANT SELECT, INSERT, UPDATE ON TABLE invitations, pending_registrations,
  identity_mail_intents TO classroom_web_identity;
GRANT SELECT, UPDATE ON TABLE identity_capacity TO classroom_web_identity;
GRANT SELECT, INSERT ON TABLE identity_audit, identity_events TO classroom_web_identity;
GRANT UPDATE (published_at) ON TABLE identity_events TO classroom_web_identity;
GRANT SELECT (id, role, state, security_epoch, normalized_email, password_hash,
  email_verified_at, deleted_at) ON TABLE users TO classroom_web_identity;
GRANT INSERT (id, normalized_email, password_hash, role, state,
  email_verified_at, profile) ON TABLE users TO classroom_web_identity;

DO $rls$
DECLARE target text;
BEGIN
  FOREACH target IN ARRAY ARRAY['invitations','pending_registrations','identity_capacity',
      'identity_mail_intents','identity_audit','identity_events'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', target);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', target);
    EXECUTE format('CREATE POLICY identity_control_plane ON public.%I TO classroom_web_identity USING (true) WITH CHECK (true)', target);
  END LOOP;
END
$rls$;

-- This admission ledger does not decide whether disabled accounts release beta
-- eligibility. It starts from existing active accounts; over-cap upgrades fail
-- transactionally, rather than silently raising the product's stage.
INSERT INTO identity_capacity (id, max_active_users, activated_count)
SELECT 1, 10, count(*) FROM users WHERE state = 'active' AND deleted_at IS NULL;

-- The identity service can create only a verified student, even with direct SQL.
-- Migration/maintenance synthetic fixtures are unaffected by this narrow guard.
CREATE FUNCTION classroom_web_guard_identity_insert() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $fn$
BEGIN
  IF pg_has_role(current_user, 'classroom_web_identity', 'USAGE')
      AND NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
    IF NEW.role IS DISTINCT FROM 'student' OR NEW.state IS DISTINCT FROM 'active'
        OR NEW.email_verified_at IS NULL OR NEW.deleted_at IS NOT NULL
        OR NEW.normalized_email IS NULL OR char_length(NEW.normalized_email) NOT BETWEEN 3 AND 254
        OR NEW.password_hash IS NULL OR NEW.password_hash NOT LIKE '$argon2id$%' THEN
      RAISE EXCEPTION 'identity activation requires a verified student' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_guard_identity_insert() FROM PUBLIC;
CREATE TRIGGER guard_identity_user_insert BEFORE INSERT ON users
  FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_identity_insert();

-- A boolean-only authentication recheck. It cannot enumerate another owner's
-- metadata, return password material, or grant a private-content RLS bypass.
CREATE FUNCTION classroom_web_principal_valid(principal_id uuid, principal_epoch bigint,
    principal_role text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
  SELECT principal_id = nullif(current_setting('app.owner_id', true), '')::uuid
    AND EXISTS (SELECT FROM public.users
      WHERE id = principal_id AND state = 'active' AND deleted_at IS NULL
        AND security_epoch = principal_epoch AND role = principal_role
        AND email_verified_at IS NOT NULL AND normalized_email IS NOT NULL
        AND password_hash IS NOT NULL)
$fn$;
REVOKE ALL ON FUNCTION classroom_web_principal_valid(uuid, bigint, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION classroom_web_principal_valid(uuid, bigint, text)
  TO classroom_web_runtime;

-- Admin validation holds a share lock through the identity transaction, so an
-- account disable cannot race past authorization. The service has no general
-- user UPDATE permission merely to acquire this lock.
CREATE FUNCTION classroom_web_identity_admin_valid(principal_id uuid,
    principal_epoch bigint) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
BEGIN
  PERFORM 1 FROM public.users
    WHERE id = principal_id AND role = 'admin' AND state = 'active'
      AND deleted_at IS NULL AND email_verified_at IS NOT NULL
      AND normalized_email IS NOT NULL AND password_hash IS NOT NULL
      AND security_epoch = principal_epoch FOR SHARE;
  RETURN FOUND;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_identity_admin_valid(uuid, bigint) FROM PUBLIC,
  classroom_web_runtime, classroom_web_scheduler, classroom_web_task_dispatcher;
GRANT EXECUTE ON FUNCTION classroom_web_identity_admin_valid(uuid, bigint)
  TO classroom_web_identity;

-- Login-bearing accounts must stay verified even when a worker/internal UoW
-- reaches RLS directly. Historical opaque, email-less owner shells remain an
-- internal provisioning boundary; principal_valid never accepts those shells.
CREATE OR REPLACE FUNCTION classroom_web_owner_active() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
  SELECT EXISTS (SELECT FROM public.users
    WHERE id = nullif(current_setting('app.owner_id', true), '')::uuid
      AND state = 'active' AND deleted_at IS NULL
      AND (normalized_email IS NULL
        OR (email_verified_at IS NOT NULL AND password_hash IS NOT NULL)))
$fn$;
REVOKE ALL ON FUNCTION classroom_web_owner_active() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION classroom_web_owner_active() TO classroom_web_runtime;

CREATE OR REPLACE FUNCTION classroom_web_lock_owner() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
DECLARE principal uuid := nullif(current_setting('app.owner_id', true), '')::uuid;
BEGIN
  IF principal IS NULL THEN
    RAISE EXCEPTION 'owner context required' USING ERRCODE = '42501';
  END IF;
  PERFORM 1 FROM public.users WHERE id = principal AND state = 'active'
    AND deleted_at IS NULL
    AND (normalized_email IS NULL
      OR (email_verified_at IS NOT NULL AND password_hash IS NOT NULL)) FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'owner unavailable' USING ERRCODE = '42501';
  END IF;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_lock_owner() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION classroom_web_lock_owner() TO classroom_web_runtime;
