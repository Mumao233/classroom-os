-- PR-04b deletion lifecycle. Forward-only; apply only through Alembic.
-- Frozen deletion_requests / deletion_tombstones columns are unchanged. Execution
-- state that the frozen model does not carry lives in deletion_executions.

-- 1. Roles ------------------------------------------------------------------
-- classroom_web_eraser owns the fixed SECURITY DEFINER functions and has no members.
-- classroom_web_deletion_worker / classroom_web_restore_operator are caller groups that
-- may only EXECUTE those functions; neither can read private tables directly.
DO $roles$
DECLARE candidate text;
BEGIN
  FOREACH candidate IN ARRAY ARRAY['classroom_web_eraser','classroom_web_deletion_worker','classroom_web_restore_operator'] LOOP
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = candidate) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS', candidate);
    ELSIF EXISTS (SELECT FROM pg_roles WHERE rolname = candidate AND
        (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolcanlogin OR rolreplication)) THEN
      RAISE EXCEPTION 'deletion groups must be unprivileged NOLOGIN';
    END IF;
    IF EXISTS (SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = candidate) THEN
      RAISE EXCEPTION 'deletion groups must not inherit other roles';
    END IF;
  END LOOP;
  IF EXISTS (SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid WHERE r.rolname = 'classroom_web_eraser') THEN
    RAISE EXCEPTION 'the eraser function owner must have no members';
  END IF;
END
$roles$;
GRANT USAGE ON SCHEMA public TO classroom_web_eraser, classroom_web_deletion_worker, classroom_web_restore_operator;
REVOKE CREATE ON SCHEMA public FROM classroom_web_eraser, classroom_web_deletion_worker, classroom_web_restore_operator;

-- 2. Scope and receipt vocabularies ---------------------------------------------
ALTER TABLE deletion_requests DROP CONSTRAINT ck_deletion_requests_scope_type;
ALTER TABLE deletion_requests ADD CONSTRAINT ck_deletion_requests_scope_type
  CHECK (scope_type IN ('semester','course','class_session','account','course_material_version','media_asset','audio'));
ALTER TABLE deletion_requests ADD CONSTRAINT ck_deletion_requests_error_code
  CHECK (last_error_code IS NULL OR char_length(last_error_code) <= 64);
ALTER TABLE deletion_requests ADD CONSTRAINT ck_deletion_requests_completion
  CHECK ((state = 'complete') = (completed_at IS NOT NULL));

ALTER TABLE operation_receipts DROP CONSTRAINT ck_operation_receipts_resource_type;
ALTER TABLE operation_receipts ADD CONSTRAINT ck_operation_receipts_resource_type
  CHECK(resource_type IN ('task','semester','course','class_session','course_material_version','media_asset','media_upload','generation_plan','question','review_document','review_revision','review_conflict','deletion_request'));

-- 3. Tables ---------------------------------------------------------------------
-- Frozen inventory columns; one row per object key. Every stored version of the key
-- is enumerated live by the worker, and only an absence proof closes the row.
CREATE TABLE deletion_assets (
    id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    owner_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    request_id uuid NOT NULL,
    asset_id uuid,
    object_key text NOT NULL CHECK (object_key ~ '^media/[a-f0-9]{32}/[a-f0-9]{32}/[a-f0-9]{64}$'),
    object_version text,
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','failed','absent')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error_code text CHECK (last_error_code IS NULL OR char_length(last_error_code) <= 64),
    confirmed_absent_at timestamptz,
    UNIQUE(owner_id,id),
    CONSTRAINT uq_deletion_asset_key UNIQUE(owner_id,request_id,object_key),
    FOREIGN KEY(owner_id,request_id) REFERENCES deletion_requests(owner_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(owner_id,asset_id) REFERENCES media_assets(owner_id,id) ON DELETE RESTRICT,
    CHECK ((state = 'absent') = (confirmed_absent_at IS NOT NULL))
);
CREATE INDEX ix_deletion_assets_request ON deletion_assets(request_id,state);

-- Implementation state for one deletion request: independent-ledger position, write
-- settle window, retry schedule and the worker lease/fence.
CREATE TABLE deletion_executions (
    request_id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    intent_id uuid NOT NULL,
    -- NULL: access was revoked locally but the independent ledger has not accepted the request.
    ledger_generation bigint CHECK (ledger_generation IS NULL OR ledger_generation >= 1),
    origin text NOT NULL CHECK (origin IN ('user','expiry')),
    settle_until timestamptz NOT NULL,
    not_before timestamptz NOT NULL,
    lease_token bigint NOT NULL DEFAULT 0 CHECK (lease_token >= 0),
    leased_by text CHECK (leased_by IS NULL OR char_length(leased_by) BETWEEN 1 AND 100),
    lease_expires_at timestamptz,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    -- Every inventoried object is proven absent. For scopes that also hold database
    -- content this is not completion: the request stays open until that content is erased.
    objects_absent_at timestamptz,
    CONSTRAINT uq_deletion_execution_intent UNIQUE(owner_id,intent_id),
    CONSTRAINT uq_deletion_execution_generation UNIQUE(ledger_generation),
    FOREIGN KEY(owner_id,request_id) REFERENCES deletion_requests(owner_id,id) ON DELETE RESTRICT,
    CHECK ((leased_by IS NULL) = (lease_expires_at IS NULL))
);
CREATE INDEX ix_deletion_executions_claim ON deletion_executions(not_before,request_id);

-- Retention authority. Ships empty: no retention period is approved (TBD-04), so no
-- asset can be assigned a policy and audio commit stays disabled by 0009's constraint.
CREATE TABLE retention_policies (
    version text PRIMARY KEY CHECK (version ~ '^[a-z0-9][a-z0-9._-]{2,63}$' AND version <> 'unassigned'),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    media_kind text NOT NULL CHECK (media_kind IN ('slide','import_package','audio_chunk','export')),
    max_lifetime interval NOT NULL CHECK (max_lifetime > interval '0'),
    approval_reference text NOT NULL CHECK (char_length(approval_reference) BETWEEN 1 AND 200),
    synthetic boolean NOT NULL
);

DO $body$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['deletion_assets','deletion_executions'] LOOP
    EXECUTE format('REVOKE ALL ON TABLE %I FROM PUBLIC',t);
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
    EXECUTE format('CREATE POLICY owner_isolation ON %I FOR SELECT TO classroom_web_runtime
      USING (owner_id = nullif(current_setting(''app.owner_id'',true),'''')::uuid AND public.classroom_web_owner_active())',t);
    EXECUTE format('CREATE POLICY eraser_all ON %I TO classroom_web_eraser USING (true) WITH CHECK (true)',t);
    EXECUTE format('GRANT SELECT ON %I TO classroom_web_runtime',t);
    EXECUTE format('GRANT SELECT,INSERT,UPDATE ON %I TO classroom_web_eraser',t);
  END LOOP;
END $body$;
REVOKE ALL ON TABLE retention_policies FROM PUBLIC;
GRANT SELECT ON retention_policies TO classroom_web_runtime, classroom_web_eraser;

-- 4. Eraser privileges: identifiers and lifecycle metadata only, never content columns.
GRANT SELECT ON deletion_requests TO classroom_web_eraser;
GRANT INSERT (id, owner_id, scope_type, scope_id, state, access_revoked_at) ON deletion_requests TO classroom_web_eraser;
GRANT UPDATE (state, completed_at, last_error_code) ON deletion_requests TO classroom_web_eraser;
CREATE POLICY eraser_all ON deletion_requests TO classroom_web_eraser USING (true) WITH CHECK (true);
GRANT SELECT (owner_id, request_id, resource_type, resource_id) ON deletion_tombstones TO classroom_web_eraser;
CREATE POLICY eraser_read ON deletion_tombstones FOR SELECT TO classroom_web_eraser USING (true);
GRANT SELECT (id, owner_id, created_at, class_session_id, kind, status, expires_at, hard_expire_at, access_revoked_at,
              deleted_at, object_key, object_version, revision, updated_at, sha256, size_bytes, mime_type,
              object_etag, first_cloud_received_at, retention_policy_version, retention_anchor_at)
  ON media_assets TO classroom_web_eraser;
GRANT UPDATE (status, access_revoked_at, deleted_at, revision, updated_at) ON media_assets TO classroom_web_eraser;
CREATE POLICY eraser_all ON media_assets TO classroom_web_eraser USING (true) WITH CHECK (true);
GRANT SELECT (owner_id, asset_id, expires_at) ON media_uploads TO classroom_web_eraser;
CREATE POLICY eraser_read ON media_uploads FOR SELECT TO classroom_web_eraser USING (true);
GRANT SELECT (id, owner_id, course_id) ON class_sessions TO classroom_web_eraser;
GRANT SELECT (id, owner_id, semester_id) ON courses TO classroom_web_eraser;
CREATE POLICY eraser_read ON class_sessions FOR SELECT TO classroom_web_eraser USING (true);
CREATE POLICY eraser_read ON courses FOR SELECT TO classroom_web_eraser USING (true);
GRANT SELECT (owner_id, next_sequence) ON owner_event_counters TO classroom_web_eraser;
GRANT INSERT (id, owner_id, next_sequence) ON owner_event_counters TO classroom_web_eraser;
GRANT UPDATE (next_sequence) ON owner_event_counters TO classroom_web_eraser;
CREATE POLICY eraser_counter ON owner_event_counters TO classroom_web_eraser USING (true) WITH CHECK (true);
GRANT INSERT (id, owner_id, event_sequence, event_type, aggregate_type, aggregate_id, aggregate_revision, payload, schema_version)
  ON outbox_events TO classroom_web_eraser;
CREATE POLICY eraser_event ON outbox_events FOR INSERT TO classroom_web_eraser WITH CHECK (true);
GRANT SELECT (id, state, deleted_at, security_epoch, revision) ON users TO classroom_web_eraser;
GRANT UPDATE (state, deleted_at, security_epoch, revision, updated_at, normalized_email, password_hash,
              email_verified_at, profile, preferences) ON users TO classroom_web_eraser;
CREATE POLICY eraser_users ON users TO classroom_web_eraser USING (true) WITH CHECK (true);
GRANT EXECUTE ON FUNCTION classroom_web_owner_active() TO classroom_web_eraser;
GRANT USAGE ON SCHEMA identity_api TO classroom_web_eraser;
GRANT EXECUTE ON FUNCTION identity_api.revoke_derived(uuid) TO classroom_web_eraser;

-- 5. Existing guards, extended additively ---------------------------------------
-- Unchanged for every caller except the eraser function owner, whose fixed functions
-- must progress requests of owners that are no longer active. A completed request and
-- an accepted request can never return to an earlier state for any role.
CREATE OR REPLACE FUNCTION classroom_web_guard_deletion_request() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $fn$
BEGIN
  IF current_user <> 'classroom_web_eraser' THEN
    PERFORM public.classroom_web_lock_owner();
    IF NEW.owner_id IS DISTINCT FROM nullif(current_setting('app.owner_id', true), '')::uuid THEN
      RAISE EXCEPTION 'owner mismatch' USING ERRCODE = '42501';
    END IF;
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
        OR NEW.scope_type IS DISTINCT FROM OLD.scope_type
        OR NEW.scope_id IS DISTINCT FROM OLD.scope_id
        OR NEW.access_revoked_at IS DISTINCT FROM OLD.access_revoked_at THEN
      RAISE EXCEPTION 'deletion scope and identity are immutable' USING ERRCODE = '23514';
    END IF;
    IF (OLD.state = 'complete' AND NEW IS DISTINCT FROM OLD)
        OR (OLD.state <> 'access_revoked' AND NEW.state = 'access_revoked') THEN
      RAISE EXCEPTION 'deletion progress is irreversible' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END
$fn$;

-- 0002 body plus the account adapter that 0001/0002 reserved for this package.
CREATE OR REPLACE FUNCTION classroom_web_guard_tombstone() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $fn$
DECLARE material_parent uuid; parent_semester uuid;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'tombstone is immutable' USING ERRCODE = '23514';
  END IF;
  PERFORM public.classroom_web_lock_owner();
  IF NEW.owner_id IS DISTINCT FROM nullif(current_setting('app.owner_id', true), '')::uuid THEN
    RAISE EXCEPTION 'owner mismatch' USING ERRCODE = '42501';
  END IF;
  IF NEW.resource_type = 'semester' THEN
    PERFORM 1 FROM public.semesters WHERE owner_id = NEW.owner_id AND id = NEW.resource_id FOR UPDATE;
  ELSIF NEW.resource_type = 'course' THEN
    PERFORM 1 FROM public.courses WHERE owner_id = NEW.owner_id AND id = NEW.resource_id FOR UPDATE;
  ELSIF NEW.resource_type = 'class_session' THEN
    PERFORM 1 FROM public.class_sessions WHERE owner_id = NEW.owner_id AND id = NEW.resource_id FOR UPDATE;
  ELSIF NEW.resource_type = 'course_material_version' THEN
    -- Immutable material identity needs no row lock. Coordinate on the course
    -- BEFORE the request lock: pointer updates already hold this course row.
    -- Lock order: owner -> semester -> course -> request -> tombstone/FKs.
    SELECT course_id INTO material_parent FROM public.course_material_versions
      WHERE owner_id = NEW.owner_id AND id = NEW.resource_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'resource unavailable' USING ERRCODE = '23514';
    END IF;
    SELECT semester_id INTO parent_semester FROM public.courses
      WHERE owner_id = NEW.owner_id AND id = material_parent;
    PERFORM 1 FROM public.semesters
      WHERE owner_id = NEW.owner_id AND id = parent_semester FOR SHARE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'material ancestor unavailable' USING ERRCODE = '23514';
    END IF;
    PERFORM 1 FROM public.courses
      WHERE owner_id = NEW.owner_id AND id = material_parent
        AND semester_id = parent_semester FOR UPDATE;
  ELSIF NEW.resource_type = 'account' THEN
    -- The account is its own resource. The deferred revocation check requires that the
    -- same transaction also seals the account through classroom_web_seal_account().
    PERFORM 1 WHERE NEW.resource_id = NEW.owner_id AND NEW.deleted_revision IS NULL;
  ELSE
    RAISE EXCEPTION 'resource type has no revocation adapter yet' USING ERRCODE = '23514';
  END IF;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'resource unavailable' USING ERRCODE = '23514';
  END IF;
  PERFORM 1 FROM public.deletion_requests WHERE owner_id = NEW.owner_id AND id = NEW.request_id
    AND scope_type = NEW.resource_type AND scope_id = NEW.resource_id FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'tombstone does not match deletion request scope' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END
$fn$;

CREATE OR REPLACE FUNCTION classroom_web_check_revocation() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $fn$
DECLARE revoked boolean; final_revision bigint;
BEGIN
  IF NEW.resource_type = 'account' THEN
    -- An account tombstone may only commit together with the account losing access.
    IF NEW.resource_id IS DISTINCT FROM NEW.owner_id
        OR NEW.owner_id IS DISTINCT FROM nullif(current_setting('app.owner_id', true), '')::uuid
        OR public.classroom_web_owner_active() THEN
      RAISE EXCEPTION 'tombstone requires atomic resource revocation' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
  ELSIF NEW.resource_type = 'semester' THEN
    SELECT deleted_at IS NOT NULL, revision INTO revoked, final_revision FROM public.semesters WHERE owner_id = NEW.owner_id AND id = NEW.resource_id;
  ELSIF NEW.resource_type = 'course' THEN
    SELECT deleted_at IS NOT NULL, revision INTO revoked, final_revision FROM public.courses WHERE owner_id = NEW.owner_id AND id = NEW.resource_id;
  ELSIF NEW.resource_type = 'class_session' THEN
    SELECT deleted_at IS NOT NULL, revision INTO revoked, final_revision FROM public.class_sessions WHERE owner_id = NEW.owner_id AND id = NEW.resource_id;
  ELSE
    -- Material revocation is represented by the immutable tombstone alone.
    revoked := true;
    SELECT revision INTO final_revision FROM public.course_material_versions
      WHERE owner_id = NEW.owner_id AND id = NEW.resource_id;
    IF EXISTS (SELECT FROM public.courses WHERE owner_id = NEW.owner_id
        AND current_material_version_id = NEW.resource_id AND deleted_at IS NULL) THEN
      RAISE EXCEPTION 'live course cannot retain revoked current material' USING ERRCODE = '23514';
    END IF;
  END IF;
  IF revoked IS DISTINCT FROM true OR NEW.deleted_revision IS DISTINCT FROM final_revision THEN
    RAISE EXCEPTION 'tombstone requires atomic resource revocation' USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END
$fn$;

-- A policy name alone can never authorize retention: the version must exist in the
-- approved table for this media kind, and both deadlines must be present and bounded.
CREATE FUNCTION classroom_web_media_retention() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $fn$
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.retention_policy_version <> 'unassigned'
      AND NEW.retention_policy_version IS DISTINCT FROM OLD.retention_policy_version THEN
    RAISE EXCEPTION 'retention policy assignment is immutable' USING ERRCODE = '23514';
  END IF;
  IF NEW.retention_policy_version <> 'unassigned' THEN
    PERFORM 1 FROM public.retention_policies p
      WHERE p.version = NEW.retention_policy_version AND p.media_kind = NEW.kind
        AND NEW.first_cloud_received_at IS NOT NULL AND NEW.retention_anchor_at IS NOT NULL
        AND NEW.expires_at IS NOT NULL AND NEW.hard_expire_at IS NOT NULL
        AND NEW.expires_at <= NEW.hard_expire_at
        AND NEW.hard_expire_at <= NEW.first_cloud_received_at + p.max_lifetime;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'media retention requires an approved policy and bounded deadlines' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NULL;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_media_retention() FROM PUBLIC;
-- AFTER: table CHECK constraints, including 0009's audio commit gate, are evaluated first.
CREATE TRIGGER media_retention AFTER INSERT OR UPDATE ON media_assets
  FOR EACH ROW EXECUTE FUNCTION classroom_web_media_retention();

-- 6. Fixed eraser functions -------------------------------------------------------
CREATE FUNCTION classroom_web_deletion_event(target_owner uuid, kind text, object uuid, deletion uuid, db_state text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
DECLARE sequence bigint;
BEGIN
  INSERT INTO public.owner_event_counters(id, owner_id, next_sequence)
    VALUES(gen_random_uuid(), target_owner, 1) ON CONFLICT DO NOTHING;
  UPDATE public.owner_event_counters c SET next_sequence = c.next_sequence + 1
    WHERE c.owner_id = target_owner RETURNING c.next_sequence - 1 INTO sequence;
  INSERT INTO public.outbox_events(id, owner_id, event_sequence, event_type,
      aggregate_type, aggregate_id, aggregate_revision, payload, schema_version)
    VALUES(gen_random_uuid(), target_owner, sequence, 'object.deleted', kind, object, NULL,
      jsonb_build_object('object_type', kind, 'object_id', object, 'deletion_id', deletion,
        'state', CASE db_state WHEN 'access_revoked' THEN 'access_revoked' WHEN 'pending' THEN 'pending_delete'
                               WHEN 'failed' THEN 'delete_failed' ELSE 'deleted' END), '1');
END
$fn$;

CREATE FUNCTION classroom_web_deletion_scope(p_owner uuid, p_kind text, p_scope uuid)
RETURNS SETOF uuid LANGUAGE sql STABLE SET search_path = public, pg_temp AS $fn$
  SELECT a.id FROM public.media_assets a WHERE a.owner_id = p_owner AND (
       (p_kind = 'media_asset' AND a.id = p_scope)
    OR (p_kind = 'audio' AND a.class_session_id = p_scope AND a.kind = 'audio_chunk')
    OR (p_kind = 'class_session' AND a.class_session_id = p_scope)
    OR (p_kind = 'course' AND a.class_session_id IN (SELECT s.id FROM public.class_sessions s
          WHERE s.owner_id = p_owner AND s.course_id = p_scope))
    OR (p_kind = 'semester' AND a.class_session_id IN (SELECT s.id FROM public.class_sessions s
          JOIN public.courses c ON c.owner_id = s.owner_id AND c.id = s.course_id
          WHERE s.owner_id = p_owner AND c.semester_id = p_scope))
    OR (p_kind = 'account' AND p_scope = p_owner))
$fn$;

-- Idempotent application of one deletion intent to media manifests. Catalog and
-- account tombstones are written by the caller in the same transaction beforehand.
CREATE FUNCTION classroom_web_revoke_media_impl(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
DECLARE
  target_owner uuid := (p->>'owner_id')::uuid;
  request uuid := (p->>'request_id')::uuid;
  intent uuid := (p->>'intent_id')::uuid;
  generation bigint := (p->>'generation')::bigint;
  scope_kind text := p->>'scope_type';
  scope uuid := (p->>'scope_id')::uuid;
  source text := p->>'origin';
  settle integer := (p->>'settle_seconds')::integer;
  n timestamptz := clock_timestamp();
  r public.deletion_requests%ROWTYPE;
  e public.deletion_executions%ROWTYPE;
  new_state text;
  changed boolean := false;
BEGIN
  IF target_owner IS NULL OR request IS NULL OR intent IS NULL OR scope IS NULL
      OR scope_kind IS NULL OR scope_kind NOT IN ('semester','course','class_session','account','media_asset','audio')
      OR source IS NULL OR source NOT IN ('user','expiry')
      OR settle IS NULL OR settle NOT BETWEEN 0 AND 3600
      OR (generation IS NOT NULL AND generation < 1)
      OR (scope_kind = 'account' AND scope <> target_owner) THEN
    RAISE EXCEPTION 'invalid deletion intent' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO r FROM public.deletion_requests d WHERE d.owner_id = target_owner AND d.id = request FOR UPDATE;
  IF NOT FOUND THEN
    new_state := CASE WHEN generation IS NULL THEN 'access_revoked' ELSE 'pending' END;
    INSERT INTO public.deletion_requests(id, owner_id, scope_type, scope_id, state, access_revoked_at)
      VALUES(request, target_owner, scope_kind, scope, new_state, n);
  ELSE
    IF r.scope_type <> scope_kind OR r.scope_id <> scope THEN
      RAISE EXCEPTION 'deletion request scope mismatch' USING ERRCODE = '23514';
    END IF;
    new_state := r.state;
    IF generation IS NOT NULL AND r.state = 'access_revoked' THEN
      UPDATE public.deletion_requests d SET state = 'pending' WHERE d.owner_id = target_owner AND d.id = request;
      new_state := 'pending';
    END IF;
  END IF;
  SELECT * INTO e FROM public.deletion_executions x WHERE x.request_id = request FOR UPDATE;
  IF NOT FOUND THEN
    INSERT INTO public.deletion_executions(request_id, owner_id, intent_id, ledger_generation, origin, settle_until, not_before)
      VALUES(request, target_owner, intent, generation, source,
             n + make_interval(secs => settle), n + make_interval(secs => settle));
    changed := true;
  ELSE
    IF e.owner_id <> target_owner OR e.intent_id <> intent THEN
      RAISE EXCEPTION 'deletion intent mismatch' USING ERRCODE = '23514';
    END IF;
    IF generation IS NOT NULL THEN
      IF e.ledger_generation IS NULL THEN
        UPDATE public.deletion_executions x SET ledger_generation = generation,
          revision = x.revision + 1, updated_at = n WHERE x.request_id = request;
        changed := true;
      ELSIF e.ledger_generation <> generation THEN
        RAISE EXCEPTION 'ledger generation conflict' USING ERRCODE = '23514';
      END IF;
    END IF;
  END IF;
  -- One deterministic row order for every caller that marks several assets.
  PERFORM 1 FROM public.media_assets a WHERE a.owner_id = target_owner
    AND a.id IN (SELECT public.classroom_web_deletion_scope(target_owner, scope_kind, scope)) ORDER BY a.id FOR UPDATE;
  UPDATE public.media_assets a SET access_revoked_at = coalesce(a.access_revoked_at, n),
      status = 'delete_pending', revision = a.revision + 1, updated_at = n
    WHERE a.owner_id = target_owner AND a.status IN ('staging','verified','committed')
      AND a.id IN (SELECT public.classroom_web_deletion_scope(target_owner, scope_kind, scope));
  INSERT INTO public.deletion_assets(id, owner_id, request_id, asset_id, object_key, object_version)
    SELECT gen_random_uuid(), target_owner, request, a.id, a.object_key, a.object_version
      FROM public.media_assets a WHERE a.owner_id = target_owner AND a.status <> 'deleted'
        AND a.id IN (SELECT public.classroom_web_deletion_scope(target_owner, scope_kind, scope))
    ON CONFLICT ON CONSTRAINT uq_deletion_asset_key DO NOTHING;
  IF changed THEN
    PERFORM public.classroom_web_deletion_event(target_owner, scope_kind, scope, request, new_state);
  END IF;
  RETURN jsonb_build_object('state', new_state, 'changed', changed);
END
$fn$;

CREATE FUNCTION classroom_web_owner_revoke_media(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
BEGIN
  IF (p->>'owner_id')::uuid IS DISTINCT FROM nullif(current_setting('app.owner_id', true), '')::uuid
      OR NOT public.classroom_web_owner_active() THEN
    RAISE EXCEPTION 'owner mismatch' USING ERRCODE = '42501';
  END IF;
  RETURN public.classroom_web_revoke_media_impl(p);
END
$fn$;

-- System path for assets whose own deadline has passed. The database clock decides;
-- the caller cannot expire an asset early or name another scope. It works for owners
-- that are disabled, so disabling an account never extends retention.
CREATE FUNCTION classroom_web_expire_media(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
BEGIN
  IF p->>'scope_type' IS DISTINCT FROM 'media_asset' OR p->>'origin' IS DISTINCT FROM 'expiry' THEN
    RAISE EXCEPTION 'only expired media may use the system path' USING ERRCODE = '42501';
  END IF;
  PERFORM 1 FROM public.media_assets a
    WHERE a.owner_id = (p->>'owner_id')::uuid AND a.id = (p->>'scope_id')::uuid
      AND (a.expires_at <= clock_timestamp() OR a.hard_expire_at <= clock_timestamp()
        OR a.access_revoked_at IS NOT NULL
        OR (a.status IN ('staging','verified') AND EXISTS (SELECT FROM public.media_uploads u
              WHERE u.owner_id = a.owner_id AND u.asset_id = a.id AND u.expires_at <= clock_timestamp())))
    FOR UPDATE;
  IF NOT FOUND AND EXISTS (SELECT FROM public.media_assets a
      WHERE a.owner_id = (p->>'owner_id')::uuid AND a.id = (p->>'scope_id')::uuid) THEN
    RAISE EXCEPTION 'asset is not expired' USING ERRCODE = '23514';
  END IF;
  -- An absent manifest row has nothing left to protect; the intent is recorded as applied.
  RETURN public.classroom_web_revoke_media_impl(p);
END
$fn$;

CREATE FUNCTION classroom_web_expired_media(batch integer)
RETURNS TABLE(owner_id uuid, asset_id uuid)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
#variable_conflict use_column
BEGIN
  IF batch IS NULL OR batch NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'invalid expiry batch limit' USING ERRCODE = '22023';
  END IF;
  RETURN QUERY SELECT a.owner_id, a.id FROM public.media_assets a
    WHERE a.status IN ('staging','verified','committed') AND (
         a.expires_at <= clock_timestamp() OR a.hard_expire_at <= clock_timestamp()
      OR (a.status = 'committed' AND a.access_revoked_at IS NOT NULL)
      OR (a.status IN ('staging','verified') AND EXISTS (SELECT FROM public.media_uploads u
            WHERE u.owner_id = a.owner_id AND u.asset_id = a.id AND u.expires_at <= clock_timestamp())))
    ORDER BY a.created_at, a.id LIMIT batch;
END
$fn$;

-- Final statement of an account deletion transaction: the account loses access in
-- the same commit as its tombstone, and every session, challenge and proof is revoked.
CREATE FUNCTION classroom_web_seal_account(request uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
DECLARE target uuid := nullif(current_setting('app.owner_id', true), '')::uuid;
        n timestamptz := clock_timestamp();
BEGIN
  IF target IS NULL OR request IS NULL OR NOT EXISTS (SELECT FROM public.deletion_tombstones t
      WHERE t.owner_id = target AND t.resource_type = 'account' AND t.resource_id = target AND t.request_id = request) THEN
    RAISE EXCEPTION 'account tombstone required' USING ERRCODE = '42501';
  END IF;
  UPDATE public.users u SET state = 'erasing', security_epoch = u.security_epoch + 1,
      revision = u.revision + 1, updated_at = n
    WHERE u.id = target AND u.state = 'active' AND u.deleted_at IS NULL;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'owner unavailable' USING ERRCODE = '42501';
  END IF;
  PERFORM identity_api.revoke_derived(target);
END
$fn$;

-- Raises SQLSTATE CWD01 unless the caller still holds the current lease fence.
CREATE FUNCTION classroom_web_deletion_fence(request uuid, token bigint) RETURNS public.deletion_executions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
DECLARE e public.deletion_executions%ROWTYPE;
BEGIN
  SELECT * INTO e FROM public.deletion_executions x WHERE x.request_id = request FOR UPDATE;
  IF NOT FOUND OR token IS NULL OR e.lease_token <> token OR e.leased_by IS NULL THEN
    RAISE EXCEPTION 'stale deletion fence' USING ERRCODE = 'CWD01';
  END IF;
  RETURN e;
END
$fn$;

CREATE FUNCTION classroom_web_claim_deletion(worker text, lease_seconds integer)
RETURNS TABLE(request_id uuid, owner_id uuid, scope_type text, lease_token bigint, attempt integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
#variable_conflict use_column
DECLARE candidate record;
BEGIN
  IF worker IS NULL OR char_length(worker) NOT BETWEEN 1 AND 100
      OR lease_seconds IS NULL OR lease_seconds NOT BETWEEN 1 AND 3600 THEN
    RAISE EXCEPTION 'invalid claim limits' USING ERRCODE = '22023';
  END IF;
  -- Only requests the independent ledger accepted are ever physically processed.
  SELECT x.request_id AS request, x.owner_id AS owner, d.scope_type AS kind, x.lease_token AS token,
      x.attempt_count AS attempts INTO candidate
    FROM public.deletion_executions x
    JOIN public.deletion_requests d ON d.owner_id = x.owner_id AND d.id = x.request_id
    WHERE d.state IN ('pending','failed') AND x.ledger_generation IS NOT NULL
      AND x.objects_absent_at IS NULL
      AND x.not_before <= clock_timestamp()
      AND (x.lease_expires_at IS NULL OR x.lease_expires_at <= clock_timestamp())
    ORDER BY x.not_before, x.request_id LIMIT 1 FOR UPDATE OF x SKIP LOCKED;
  IF NOT FOUND THEN
    RETURN;
  END IF;
  UPDATE public.deletion_executions x SET lease_token = candidate.token + 1, leased_by = worker,
      lease_expires_at = clock_timestamp() + make_interval(secs => lease_seconds),
      attempt_count = x.attempt_count + 1, revision = x.revision + 1, updated_at = clock_timestamp()
    WHERE x.request_id = candidate.request;
  request_id := candidate.request; owner_id := candidate.owner;
  scope_type := candidate.kind; lease_token := candidate.token + 1; attempt := candidate.attempts + 1;
  RETURN NEXT;
END
$fn$;

CREATE FUNCTION classroom_web_deletion_inventory(request uuid, token bigint)
RETURNS TABLE(asset_row uuid, object_key text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
#variable_conflict use_column
BEGIN
  PERFORM public.classroom_web_deletion_fence(request, token);
  RETURN QUERY SELECT a.id, a.object_key FROM public.deletion_assets a
    WHERE a.request_id = request AND a.state <> 'absent' ORDER BY a.object_key;
END
$fn$;

-- An absence proof recorded before the write settle window closes is refused
-- (SQLSTATE CWD02): an upload that began before revocation may still land.
CREATE FUNCTION classroom_web_record_deletion(request uuid, token bigint, asset_row uuid, outcome text, code text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
DECLARE e public.deletion_executions%ROWTYPE;
BEGIN
  e := public.classroom_web_deletion_fence(request, token);
  IF outcome = 'absent' THEN
    IF clock_timestamp() < e.settle_until THEN
      RAISE EXCEPTION 'write settle window is still open' USING ERRCODE = 'CWD02';
    END IF;
    UPDATE public.deletion_assets a SET state = 'absent', confirmed_absent_at = clock_timestamp(),
        attempt_count = a.attempt_count + 1, last_error_code = NULL
      WHERE a.id = asset_row AND a.request_id = request AND a.state <> 'absent';
  ELSIF outcome = 'failed' AND code IS NOT NULL AND char_length(code) BETWEEN 1 AND 64 THEN
    UPDATE public.deletion_assets a SET state = 'failed', attempt_count = a.attempt_count + 1, last_error_code = code
      WHERE a.id = asset_row AND a.request_id = request AND a.state <> 'absent';
  ELSE
    RAISE EXCEPTION 'invalid deletion outcome' USING ERRCODE = '22023';
  END IF;
END
$fn$;

CREATE FUNCTION classroom_web_finish_deletion(request uuid, token bigint, backoff_seconds integer)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
DECLARE e public.deletion_executions%ROWTYPE;
        r public.deletion_requests%ROWTYPE;
        open_items integer;
        failure text;
        n timestamptz := clock_timestamp();
BEGIN
  IF backoff_seconds IS NULL OR backoff_seconds NOT BETWEEN 0 AND 86400 THEN
    RAISE EXCEPTION 'invalid retry delay' USING ERRCODE = '22023';
  END IF;
  e := public.classroom_web_deletion_fence(request, token);
  SELECT * INTO r FROM public.deletion_requests d WHERE d.owner_id = e.owner_id AND d.id = request FOR UPDATE;
  SELECT count(*), min(a.last_error_code) INTO open_items, failure
    FROM public.deletion_assets a WHERE a.request_id = request AND a.state <> 'absent';
  IF open_items = 0 AND n >= e.settle_until THEN
    PERFORM 1 FROM public.media_assets a WHERE a.owner_id = e.owner_id AND a.status <> 'deleted'
      AND a.id IN (SELECT d.asset_id FROM public.deletion_assets d WHERE d.request_id = request AND d.asset_id IS NOT NULL)
      ORDER BY a.id FOR UPDATE;
    UPDATE public.media_assets a SET status = 'deleted', deleted_at = coalesce(a.deleted_at, n),
        access_revoked_at = coalesce(a.access_revoked_at, n), revision = a.revision + 1, updated_at = n
      WHERE a.owner_id = e.owner_id AND a.status <> 'deleted'
        AND a.id IN (SELECT d.asset_id FROM public.deletion_assets d WHERE d.request_id = request AND d.asset_id IS NOT NULL);
    IF r.scope_type = 'account' THEN
      -- Keep only the opaque shell that tombstones and metering rows reference.
      UPDATE public.users u SET state = 'erased', deleted_at = n, normalized_email = NULL, password_hash = NULL,
          email_verified_at = NULL, profile = '{}'::jsonb, preferences = '{}'::jsonb,
          revision = u.revision + 1, updated_at = n
        WHERE u.id = e.owner_id AND u.state = 'erasing';
    END IF;
    IF r.scope_type NOT IN ('audio','media_asset') THEN
      -- A classroom, course, semester or account also holds database content
      -- (transcripts, provider output, materials, questions, review documents). This
      -- package erases objects only, so the request is not reported as deleted: it
      -- stays pending, access stays revoked, and the worker no longer claims it.
      UPDATE public.deletion_requests d SET state = 'pending', last_error_code = NULL
        WHERE d.owner_id = e.owner_id AND d.id = request;
      UPDATE public.deletion_executions x SET lease_token = x.lease_token + 1, leased_by = NULL,
          lease_expires_at = NULL, objects_absent_at = n, revision = x.revision + 1, updated_at = n
        WHERE x.request_id = request;
      IF r.state <> 'pending' THEN
        PERFORM public.classroom_web_deletion_event(e.owner_id, r.scope_type, r.scope_id, request, 'pending');
      END IF;
      RETURN 'objects_absent';
    END IF;
    UPDATE public.deletion_requests d SET state = 'complete', completed_at = n, last_error_code = NULL
      WHERE d.owner_id = e.owner_id AND d.id = request;
    UPDATE public.deletion_executions x SET lease_token = x.lease_token + 1, leased_by = NULL,
        lease_expires_at = NULL, revision = x.revision + 1, updated_at = n WHERE x.request_id = request;
    PERFORM public.classroom_web_deletion_event(e.owner_id, r.scope_type, r.scope_id, request, 'complete');
    RETURN 'complete';
  END IF;
  UPDATE public.media_assets a SET status = 'delete_failed', revision = a.revision + 1, updated_at = n
    WHERE a.owner_id = e.owner_id AND a.status = 'delete_pending'
      AND a.id IN (SELECT d.asset_id FROM public.deletion_assets d
                   WHERE d.request_id = request AND d.asset_id IS NOT NULL AND d.state = 'failed');
  UPDATE public.deletion_requests d SET state = 'failed', last_error_code = coalesce(failure, 'DELETE_INCOMPLETE')
    WHERE d.owner_id = e.owner_id AND d.id = request;
  UPDATE public.deletion_executions x SET lease_token = x.lease_token + 1, leased_by = NULL,
      lease_expires_at = NULL, not_before = n + make_interval(secs => backoff_seconds),
      revision = x.revision + 1, updated_at = n WHERE x.request_id = request;
  IF r.state <> 'failed' THEN
    PERFORM public.classroom_web_deletion_event(e.owner_id, r.scope_type, r.scope_id, request, 'failed');
  END IF;
  RETURN 'failed';
END
$fn$;

-- Identifiers of every local deletion, for comparison with the independent ledger.
CREATE FUNCTION classroom_web_deletion_ledger_state()
RETURNS TABLE(request_id uuid, owner_id uuid, intent_id uuid, ledger_generation bigint,
              scope_type text, scope_id uuid, origin text, state text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
#variable_conflict use_column
BEGIN
  RETURN QUERY SELECT x.request_id, x.owner_id, x.intent_id, x.ledger_generation,
      d.scope_type, d.scope_id, x.origin, d.state
    FROM public.deletion_executions x
    JOIN public.deletion_requests d ON d.owner_id = x.owner_id AND d.id = x.request_id
    ORDER BY x.created_at, x.request_id;
END
$fn$;

-- live: a readable or staged manifest owns the key; pending: deletion is in progress;
-- deleted: absence was already proven; unknown: no manifest row names the key.
CREATE FUNCTION classroom_web_object_key_state(key text) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
  SELECT coalesce((SELECT CASE WHEN a.status = 'deleted' THEN 'deleted'
                               WHEN a.status IN ('delete_pending','delete_failed') THEN 'pending'
                               ELSE 'live' END
                   FROM public.media_assets a WHERE a.object_key = key), 'unknown')
$fn$;

-- Lets reconciliation tell an owner the restored copy never had from one that is disabled.
CREATE FUNCTION classroom_web_owner_state(target uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
  SELECT coalesce((SELECT u.state FROM public.users u WHERE u.id = target), 'absent')
$fn$;

-- Counts of state that must be zero before a restored database may serve requests.
CREATE FUNCTION classroom_web_deletion_audit() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
  SELECT jsonb_build_object(
    'exposed_media', (SELECT count(*) FROM public.media_assets a
      WHERE a.status IN ('staging','verified','committed') AND a.access_revoked_at IS NULL AND (
        EXISTS (SELECT FROM public.deletion_tombstones t WHERE t.owner_id = a.owner_id
                  AND t.resource_type = 'account' AND t.resource_id = a.owner_id)
        OR EXISTS (SELECT FROM public.class_sessions s
              JOIN public.courses c ON c.owner_id = s.owner_id AND c.id = s.course_id
              JOIN public.deletion_tombstones t ON t.owner_id = s.owner_id AND (
                   (t.resource_type = 'class_session' AND t.resource_id = s.id)
                OR (t.resource_type = 'course' AND t.resource_id = c.id)
                OR (t.resource_type = 'semester' AND t.resource_id = c.semester_id))
              WHERE s.owner_id = a.owner_id AND s.id = a.class_session_id))),
    'unaccepted_requests', (SELECT count(*) FROM public.deletion_executions x WHERE x.ledger_generation IS NULL),
    'open_requests', (SELECT count(*) FROM public.deletion_requests d WHERE d.state <> 'complete'),
    'content_erasure_outstanding', (SELECT count(*) FROM public.deletion_executions x
      JOIN public.deletion_requests d ON d.owner_id = x.owner_id AND d.id = x.request_id
      WHERE x.objects_absent_at IS NOT NULL AND d.state <> 'complete'),
    'failed_requests', (SELECT count(*) FROM public.deletion_requests d WHERE d.state = 'failed'),
    'erasing_accounts', (SELECT count(*) FROM public.users u WHERE u.state = 'erasing'))
$fn$;

-- Restore only: every credential-derived grant in the restored copy predates the
-- incident and is revoked; every worker lease in it belongs to a process that is gone.
CREATE FUNCTION classroom_web_restore_seal() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
DECLARE target record; sealed integer := 0; leases integer; n timestamptz := clock_timestamp();
BEGIN
  FOR target IN SELECT u.id FROM public.users u WHERE u.state IN ('active','disabled') ORDER BY u.id FOR UPDATE LOOP
    UPDATE public.users u SET security_epoch = u.security_epoch + 1, revision = u.revision + 1, updated_at = n
      WHERE u.id = target.id;
    PERFORM identity_api.revoke_derived(target.id);
    sealed := sealed + 1;
  END LOOP;
  UPDATE public.deletion_executions x SET lease_token = x.lease_token + 1, leased_by = NULL,
      lease_expires_at = NULL, revision = x.revision + 1, updated_at = n WHERE x.leased_by IS NOT NULL;
  GET DIAGNOSTICS leases = ROW_COUNT;
  RETURN jsonb_build_object('accounts_sealed', sealed, 'leases_cleared', leases);
END
$fn$;

-- Restore only: accounts the operator attests lost access after the backup was taken.
-- The independent ledger does not journal account disablement, so the restored copy
-- cannot know them; an unknown or already inactive identifier is counted, not an error.
CREATE FUNCTION classroom_web_restore_block(targets uuid[]) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
DECLARE target record; blocked integer := 0; n timestamptz := clock_timestamp();
BEGIN
  IF targets IS NULL OR cardinality(targets) > 10000 THEN
    RAISE EXCEPTION 'invalid account list' USING ERRCODE = '22023';
  END IF;
  FOR target IN SELECT u.id FROM public.users u WHERE u.id = ANY(targets) AND u.state = 'active'
      ORDER BY u.id FOR UPDATE LOOP
    UPDATE public.users u SET state = 'disabled', security_epoch = u.security_epoch + 1,
        revision = u.revision + 1, updated_at = n WHERE u.id = target.id;
    PERFORM identity_api.revoke_derived(target.id);
    blocked := blocked + 1;
  END LOOP;
  RETURN jsonb_build_object('requested', cardinality(targets), 'blocked', blocked);
END
$fn$;

-- 7. Function ownership and EXECUTE grants ---------------------------------------
GRANT CREATE ON SCHEMA public TO classroom_web_eraser;
DO $functions$
DECLARE item record;
BEGIN
  FOR item IN SELECT p.oid::regprocedure AS signature FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
      WHERE n.nspname = 'public' AND p.proname IN (
        'classroom_web_deletion_event','classroom_web_deletion_scope','classroom_web_revoke_media_impl',
        'classroom_web_owner_revoke_media','classroom_web_expire_media','classroom_web_expired_media',
        'classroom_web_seal_account','classroom_web_deletion_fence','classroom_web_claim_deletion',
        'classroom_web_deletion_inventory','classroom_web_record_deletion','classroom_web_finish_deletion',
        'classroom_web_deletion_ledger_state','classroom_web_object_key_state','classroom_web_deletion_audit',
        'classroom_web_owner_state',
        'classroom_web_restore_seal','classroom_web_restore_block') LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO classroom_web_eraser', item.signature);
    EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC, classroom_web_runtime, classroom_web_deletion_worker, classroom_web_restore_operator', item.signature);
  END LOOP;
END
$functions$;
REVOKE CREATE ON SCHEMA public FROM classroom_web_eraser;
GRANT EXECUTE ON FUNCTION classroom_web_owner_revoke_media(jsonb), classroom_web_seal_account(uuid) TO classroom_web_runtime;
GRANT EXECUTE ON FUNCTION classroom_web_expire_media(jsonb), classroom_web_expired_media(integer),
  classroom_web_claim_deletion(text, integer), classroom_web_deletion_inventory(uuid, bigint),
  classroom_web_record_deletion(uuid, bigint, uuid, text, text), classroom_web_finish_deletion(uuid, bigint, integer),
  classroom_web_deletion_ledger_state(), classroom_web_object_key_state(text), classroom_web_deletion_audit(),
  classroom_web_owner_state(uuid) TO classroom_web_deletion_worker;
GRANT EXECUTE ON FUNCTION classroom_web_restore_seal(), classroom_web_restore_block(uuid[]), classroom_web_deletion_audit(),
  classroom_web_deletion_ledger_state() TO classroom_web_restore_operator;
