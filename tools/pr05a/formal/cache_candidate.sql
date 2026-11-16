-- PR-05a additive candidate, applied only to an owned synthetic database.
-- Formal Alembic revision and public contract version remain coordination items.
CREATE TABLE public.cache_keys (
    id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    owner_id uuid NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
    device_id uuid NOT NULL,
    purpose text NOT NULL CHECK (purpose IN ('learning','draft','recording')),
    class_session_id uuid,
    wrapped_dek bytea NOT NULL CHECK (octet_length(wrapped_dek)=61),
    kek_version text NOT NULL CHECK (char_length(kek_version) BETWEEN 1 AND 64),
    key_version integer NOT NULL CHECK (key_version>=1),
    revoked_at timestamptz,
    UNIQUE (owner_id,id),
    UNIQUE (owner_id,device_id,purpose,class_session_id,key_version),
    FOREIGN KEY (owner_id,device_id) REFERENCES public.devices(owner_id,id) ON DELETE RESTRICT,
    FOREIGN KEY (owner_id,class_session_id) REFERENCES public.class_sessions(owner_id,id) ON DELETE RESTRICT,
    CHECK ((purpose='recording')=(class_session_id IS NOT NULL))
);
CREATE UNIQUE INDEX cache_keys_without_classroom ON public.cache_keys(owner_id,device_id,purpose,key_version)
    WHERE class_session_id IS NULL;
ALTER TABLE public.cache_keys OWNER TO classroom_web_identity_table_owner;
ALTER TABLE public.cache_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cache_keys FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.cache_keys FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE(id) ON public.cache_keys TO classroom_web_runtime;
CREATE POLICY cache_owner ON public.cache_keys TO classroom_web_runtime
    USING (owner_id=nullif(current_setting('app.owner_id',true),'')::uuid AND public.classroom_web_owner_active())
    WITH CHECK (owner_id=nullif(current_setting('app.owner_id',true),'')::uuid AND public.classroom_web_owner_active());
CREATE FUNCTION public.cache_key_identity_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $f$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id THEN RAISE EXCEPTION 'cache key identity is immutable';END IF;
    RETURN NEW;
END $f$;
CREATE TRIGGER cache_key_identity_immutable BEFORE UPDATE ON public.cache_keys
    FOR EACH ROW EXECUTE FUNCTION public.cache_key_identity_immutable();

CREATE TABLE identity_control.cache_policy (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    enabled boolean NOT NULL DEFAULT false,
    history_enabled boolean NOT NULL DEFAULT false,
    policy_revision text,
    unlock_window_seconds integer,
    reauth_window_seconds integer,
    CHECK (NOT enabled OR (policy_revision IS NOT NULL AND char_length(policy_revision) BETWEEN 1 AND 64
        AND unlock_window_seconds IS NOT NULL AND reauth_window_seconds IS NOT NULL
        AND unlock_window_seconds BETWEEN 1 AND 600 AND reauth_window_seconds BETWEEN 1 AND 600))
);
INSERT INTO identity_control.cache_policy(singleton) VALUES(true);
ALTER TABLE identity_control.cache_policy OWNER TO classroom_web_identity_table_owner;
REVOKE ALL ON identity_control.cache_policy FROM PUBLIC,classroom_web_runtime,classroom_web_identity;
-- PostgreSQL row locking needs UPDATE on at least one column. The singleton
-- identity is fixed by CHECK(singleton); configuration columns remain read-only.
GRANT SELECT,UPDATE(singleton) ON identity_control.cache_policy TO classroom_web_session_writer;

-- This only permits an explicit future trust implementation. trustDevice remains disabled.
ALTER TABLE public.devices DROP CONSTRAINT devices_trusted_check;

CREATE FUNCTION public.classroom_web_authorize_cache_key(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE uid uuid:=(p->>'owner_id')::uuid;did uuid:=(p->>'device_id')::uuid;
    sid uuid:=(p->>'session_id')::uuid;d public.devices%ROWTYPE;r public.reauth_proofs%ROWTYPE;
    policy identity_control.cache_policy%ROWTYPE;n timestamptz;until_at timestamptz;
BEGIN
    IF uid IS DISTINCT FROM nullif(current_setting('app.owner_id',true),'')::uuid THEN RETURN NULL;END IF;
    PERFORM id FROM public.users WHERE id=uid AND state='active' AND deleted_at IS NULL
        AND email_verified_at IS NOT NULL AND security_epoch=(p->>'security_epoch')::bigint AND role=p->>'role' FOR SHARE;
    IF NOT FOUND OR NOT public.classroom_web_session_valid(uid,sid,did,(p->>'security_epoch')::bigint,p->>'role') THEN RETURN NULL;END IF;
    SELECT * INTO policy FROM identity_control.cache_policy WHERE singleton FOR SHARE;
    IF NOT FOUND OR NOT policy.enabled OR (p->>'purpose'='recoverCache' AND NOT policy.history_enabled)
        THEN RETURN jsonb_build_object('error','SERVICE_DISABLED');END IF;
    IF p->>'purpose' IS NULL OR p->>'scope' IS NULL OR p->>'purpose' NOT IN ('unlockCache','recoverCache') OR p->>'scope' NOT IN ('learning','drafts','recording_recovery')
        THEN RETURN jsonb_build_object('error','FORBIDDEN');END IF;
    SELECT * INTO d FROM public.devices WHERE owner_id=uid AND id=did FOR SHARE;
    IF d.id IS NULL OR d.revoked_at IS NOT NULL OR d.key_epoch IS DISTINCT FROM (p->>'key_epoch')::bigint
        OR (p->>'scope' IN ('learning','drafts') AND NOT d.trusted) THEN RETURN jsonb_build_object('error','FORBIDDEN');END IF;
    SELECT * INTO r FROM public.reauth_proofs WHERE token_hash=p->>'reauth_hash' AND owner_id=uid FOR UPDATE;
    n:=clock_timestamp();
    IF r.id IS NULL OR r.session_id<>sid OR r.device_id<>did OR r.security_epoch<>(p->>'security_epoch')::bigint
        OR r.purpose IS DISTINCT FROM p->>'purpose' OR r.target_id<>did OR r.commitment IS DISTINCT FROM p->>'commitment'
        OR r.operation_key IS DISTINCT FROM (p->>'operation_key')::uuid OR r.consumed_at IS NOT NULL
        OR n>=least(r.expires_at,r.verified_at+make_interval(secs=>policy.reauth_window_seconds))
        THEN RETURN jsonb_build_object('error','REAUTH_REQUIRED');END IF;
    SELECT least(n+make_interval(secs=>policy.unlock_window_seconds),r.expires_at,s.idle_deadline,s.absolute_deadline)
        INTO until_at FROM public.auth_sessions s WHERE s.id=sid AND s.owner_id=uid;
    IF until_at IS NULL OR n>=until_at THEN RETURN jsonb_build_object('error','REAUTH_REQUIRED');END IF;
    UPDATE public.reauth_proofs SET consumed_at=n WHERE id=r.id;
    RETURN jsonb_build_object('expires_at',until_at,'policy_revision',policy.policy_revision);
END $f$;
ALTER FUNCTION public.classroom_web_authorize_cache_key(jsonb) OWNER TO classroom_web_session_writer;
REVOKE ALL ON FUNCTION public.classroom_web_authorize_cache_key(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.classroom_web_authorize_cache_key(jsonb) TO classroom_web_runtime;
