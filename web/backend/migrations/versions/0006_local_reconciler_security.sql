-- R2 DESIGN APPROVE, 2026-11-02: three isolated, non-login function owners.
-- Function bodies are replaced earlier in this same revision, before transfer.
-- This script never changes an existing runtime/dispatcher policy or grant.

DO $preflight$
DECLARE actor_super boolean;
        object record;
        target oid;
        signature text;
        candidate text;
        role_oid oid;
BEGIN
  SELECT rolsuper INTO actor_super FROM pg_roles WHERE rolname = current_user;
  IF NOT actor_super AND EXISTS (
    SELECT FROM pg_roles r WHERE r.rolname IN ('classroom_web_runtime',
      'classroom_web_scheduler','classroom_web_task_dispatcher','classroom_web_identity')
      AND pg_has_role(current_user, r.oid, 'MEMBER')) THEN
    RAISE EXCEPTION 'local reconciliation migration requires a separate DDL identity';
  END IF;

  -- CREATEROLE is not ownership of bootstrap-owned functions/tables/schema.
  -- A controlled non-superuser needs explicit prior object management rights.
  IF NOT actor_super THEN
    IF NOT EXISTS (SELECT FROM pg_namespace n WHERE n.nspname = 'public'
        AND pg_has_role(current_user, n.nspowner, 'USAGE')) THEN
      RAISE EXCEPTION 'local reconciliation migration requires schema management';
    END IF;
    FOR object IN SELECT c.relowner FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relname IN ('users','tasks','task_attempts',
        'provider_results','semesters','courses','class_sessions','course_material_versions',
        'deletion_tombstones','owner_event_counters','outbox_events') LOOP
      IF NOT pg_has_role(current_user, object.relowner, 'USAGE') THEN
        RAISE EXCEPTION 'local reconciliation migration requires table management';
      END IF;
    END LOOP;
  END IF;

  FOREACH signature IN ARRAY ARRAY[
    'public.classroom_web_identity_usable(uuid)',
    'public.classroom_web_attempt_submitted(uuid,uuid)',
    'public.classroom_web_local_reconcile_candidates(integer)',
    'public.classroom_web_reconcile_saved_result(uuid,bigint)',
    'public.classroom_web_local_json_payload(text)',
    'public.classroom_web_local_json_token(text)',
    'public.classroom_web_local_float_token(text)'] LOOP
    target := to_regprocedure(signature);
    IF target IS NULL THEN
      RAISE EXCEPTION 'local reconciliation migration requires the 0005 functions';
    END IF;
    IF NOT actor_super AND NOT EXISTS (SELECT FROM pg_proc p WHERE p.oid=target
        AND pg_has_role(current_user, p.proowner, 'USAGE')) THEN
      RAISE EXCEPTION 'local reconciliation migration requires function management';
    END IF;
    -- Do not silently preserve or remove unknown default/explicit grantees.
    IF EXISTS (SELECT FROM pg_proc p CROSS JOIN LATERAL
        aclexplode(coalesce(p.proacl, acldefault('f',p.proowner))) a
        LEFT JOIN pg_roles r ON r.oid=a.grantee
      WHERE p.oid=target AND (a.privilege_type <> 'EXECUTE' OR a.is_grantable
        AND a.grantee <> p.proowner OR a.grantee <> p.proowner AND NOT (
          (signature IN ('public.classroom_web_identity_usable(uuid)',
             'public.classroom_web_attempt_submitted(uuid,uuid)')
            AND coalesce(r.rolname,'')='classroom_web_task_dispatcher')
          OR (signature IN ('public.classroom_web_local_reconcile_candidates(integer)',
             'public.classroom_web_reconcile_saved_result(uuid,bigint)')
            AND coalesce(r.rolname,'')='classroom_web_runtime')))) THEN
      RAISE EXCEPTION 'local reconciliation function has an unexpected ACL';
    END IF;
  END LOOP;

  IF EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      CROSS JOIN LATERAL aclexplode(c.relacl) a
      WHERE n.nspname='public' AND a.grantee=0)
    OR EXISTS (SELECT FROM pg_attribute c JOIN pg_class t ON t.oid=c.attrelid
      JOIN pg_namespace n ON n.oid=t.relnamespace
      CROSS JOIN LATERAL aclexplode(c.attacl) a
      WHERE n.nspname='public' AND a.grantee=0) THEN
    RAISE EXCEPTION 'PUBLIC project table authority is incompatible with isolated function owners';
  END IF;
  IF EXISTS (SELECT FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
      JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND 0::oid=ANY(p.polroles)) THEN
    RAISE EXCEPTION 'PUBLIC project policy is incompatible with isolated function owners';
  END IF;

  FOREACH candidate IN ARRAY ARRAY['classroom_web_identity_checker',
      'classroom_web_recovery_metadata','classroom_web_local_result_processor'] LOOP
    SELECT oid INTO role_oid FROM pg_roles WHERE rolname=candidate;
    IF role_oid IS NULL THEN CONTINUE; END IF;
    IF EXISTS (SELECT FROM pg_roles WHERE oid=role_oid AND
        (rolsuper OR rolbypassrls OR rolcanlogin OR rolinherit OR rolcreatedb
          OR rolcreaterole OR rolreplication OR rolconfig IS NOT NULL)) THEN
      RAISE EXCEPTION 'local reconciliation function owner must be isolated NOLOGIN';
    END IF;
    -- Only the current DDL actor's explicit temporary provisioning is allowed.
    -- It is removed before commit, including PG17 automatic creator ADMIN.
    IF EXISTS (SELECT FROM pg_auth_members WHERE member=role_oid
        OR roleid=role_oid AND member <> current_user::regrole) THEN
      RAISE EXCEPTION 'local reconciliation function owner has unexpected membership';
    END IF;
    IF EXISTS (SELECT FROM pg_class WHERE relowner=role_oid)
      OR EXISTS (SELECT FROM pg_namespace WHERE nspowner=role_oid)
      OR EXISTS (SELECT FROM pg_database WHERE datdba=role_oid)
      OR EXISTS (SELECT FROM pg_proc WHERE proowner=role_oid)
      OR EXISTS (SELECT FROM pg_default_acl d WHERE d.defaclrole=role_oid
          OR EXISTS (SELECT FROM aclexplode(d.defaclacl) a WHERE a.grantee=role_oid)) THEN
      RAISE EXCEPTION 'pre-existing reconciliation role owns unexpected objects or defaults';
    END IF;
    IF EXISTS (SELECT FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
        WHERE a.grantee=role_oid)
      OR EXISTS (SELECT FROM pg_attribute c CROSS JOIN LATERAL aclexplode(c.attacl) a
        WHERE a.grantee=role_oid)
      OR EXISTS (SELECT FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a
        WHERE a.grantee=role_oid)
      OR EXISTS (SELECT FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a
        WHERE a.grantee=role_oid AND (n.nspname <> 'public' OR a.privilege_type <> 'USAGE'))
      OR EXISTS (SELECT FROM pg_database d CROSS JOIN LATERAL aclexplode(d.datacl) a
        WHERE a.grantee=role_oid)
      OR EXISTS (SELECT FROM pg_policy WHERE role_oid=ANY(polroles))
      OR has_schema_privilege(candidate,'public','CREATE') THEN
      RAISE EXCEPTION 'pre-existing reconciliation role has unexpected authority';
    END IF;
  END LOOP;
END
$preflight$;

DO $owners$
DECLARE candidate text;
        actor_super boolean;
BEGIN
  SELECT rolsuper INTO actor_super FROM pg_roles WHERE rolname=current_user;
  FOREACH candidate IN ARRAY ARRAY['classroom_web_identity_checker',
      'classroom_web_recovery_metadata','classroom_web_local_result_processor'] LOOP
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname=candidate) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION', candidate);
    END IF;
    IF NOT actor_super THEN
      -- Required by ALTER OWNER; no inheritance is needed for the transfer.
      EXECUTE format('GRANT %I TO %I WITH INHERIT FALSE, SET TRUE', candidate, current_user);
    END IF;
    EXECUTE format('GRANT USAGE, CREATE ON SCHEMA public TO %I',candidate);
  END LOOP;
END
$owners$;

GRANT SELECT (id,state,deleted_at,normalized_email,email_verified_at,password_hash)
  ON public.users TO classroom_web_identity_checker;

GRANT SELECT (id,owner_id,kind,lease_token,state,not_before,lease_expires_at,updated_at,error_code)
  ON public.tasks TO classroom_web_recovery_metadata;
GRANT SELECT (id,owner_id,task_id,attempt_number,raw_output_ref,submitted_at,
  provider_request_id,provider_outcome,state)
  ON public.task_attempts TO classroom_web_recovery_metadata;
GRANT SELECT (id,owner_id,attempt_id,validation_status,conflict_sha256)
  ON public.provider_results TO classroom_web_recovery_metadata;

CREATE POLICY recovery_metadata_select ON public.tasks
  FOR SELECT TO classroom_web_recovery_metadata USING (true);
CREATE POLICY recovery_metadata_select ON public.task_attempts
  FOR SELECT TO classroom_web_recovery_metadata USING (true);
CREATE POLICY recovery_metadata_select ON public.provider_results
  FOR SELECT TO classroom_web_recovery_metadata USING (true);

GRANT SELECT (id,owner_id,kind,state,lease_token,lease_expires_at,revision,
  class_session_id,not_before,error_code,may_have_charged)
  ON public.tasks TO classroom_web_local_result_processor;
GRANT UPDATE (state,error_code,may_have_charged,lease_token,leased_by,
  lease_expires_at,revision,updated_at,completed_at)
  ON public.tasks TO classroom_web_local_result_processor;
GRANT SELECT (id,owner_id,task_id,attempt_number,raw_output_ref,provider_outcome,state,finished_at)
  ON public.task_attempts TO classroom_web_local_result_processor;
GRANT UPDATE (state,raw_output_ref,provider_outcome,error_code,finished_at)
  ON public.task_attempts TO classroom_web_local_result_processor;
GRANT SELECT (id,owner_id,attempt_id,raw_output,validation_status,conflict_sha256)
  ON public.provider_results TO classroom_web_local_result_processor;
GRANT UPDATE (validation_status,validated_payload)
  ON public.provider_results TO classroom_web_local_result_processor;

GRANT SELECT (id,owner_id,deleted_at,revision)
  ON public.semesters TO classroom_web_local_result_processor;
GRANT SELECT (id,owner_id,semester_id,deleted_at,revision)
  ON public.courses TO classroom_web_local_result_processor;
GRANT SELECT (id,owner_id,course_id,course_material_version_id,deleted_at,revision)
  ON public.class_sessions TO classroom_web_local_result_processor;
GRANT UPDATE (id) ON public.semesters, public.courses, public.class_sessions
  TO classroom_web_local_result_processor;
GRANT SELECT (id,owner_id,course_id)
  ON public.course_material_versions TO classroom_web_local_result_processor;
GRANT SELECT (owner_id,resource_type,resource_id)
  ON public.deletion_tombstones TO classroom_web_local_result_processor;
GRANT SELECT (owner_id,next_sequence)
  ON public.owner_event_counters TO classroom_web_local_result_processor;
GRANT INSERT (id,owner_id,next_sequence)
  ON public.owner_event_counters TO classroom_web_local_result_processor;
GRANT UPDATE (next_sequence)
  ON public.owner_event_counters TO classroom_web_local_result_processor;
GRANT INSERT (id,owner_id,event_sequence,event_type,aggregate_type,aggregate_id,
  aggregate_revision,payload,schema_version)
  ON public.outbox_events TO classroom_web_local_result_processor;

-- No FOR ALL policy: permissive policies are OR-combined. Source lock policies
-- admit the existing owner row, but CHECK(false) rejects every actual write.
DO $processor_policies$
DECLARE target text;
        owner_predicate text := 'owner_id = nullif(current_setting(''app.owner_id'',true),'''')::uuid';
BEGIN
  FOREACH target IN ARRAY ARRAY['tasks','task_attempts','provider_results',
      'semesters','courses','class_sessions','course_material_versions',
      'deletion_tombstones','owner_event_counters'] LOOP
    EXECUTE format('CREATE POLICY local_processor_select ON public.%I FOR SELECT TO classroom_web_local_result_processor USING (%s)', target, owner_predicate);
  END LOOP;
  FOREACH target IN ARRAY ARRAY['tasks','task_attempts','provider_results','owner_event_counters'] LOOP
    EXECUTE format('CREATE POLICY local_processor_update ON public.%I FOR UPDATE TO classroom_web_local_result_processor USING (%s) WITH CHECK (%s)', target, owner_predicate, owner_predicate);
  END LOOP;
  FOREACH target IN ARRAY ARRAY['semesters','courses','class_sessions'] LOOP
    EXECUTE format('CREATE POLICY local_processor_lock ON public.%I FOR UPDATE TO classroom_web_local_result_processor USING (%s) WITH CHECK (false)', target, owner_predicate);
  END LOOP;
  FOREACH target IN ARRAY ARRAY['owner_event_counters','outbox_events'] LOOP
    EXECUTE format('CREATE POLICY local_processor_insert ON public.%I FOR INSERT TO classroom_web_local_result_processor WITH CHECK (%s)', target, owner_predicate);
  END LOOP;
END
$processor_policies$;

REVOKE ALL ON FUNCTION public.classroom_web_identity_usable(uuid) FROM PUBLIC,
  classroom_web_runtime,classroom_web_scheduler,classroom_web_identity;
GRANT EXECUTE ON FUNCTION public.classroom_web_identity_usable(uuid)
  TO classroom_web_task_dispatcher,classroom_web_recovery_metadata,classroom_web_local_result_processor;

REVOKE ALL ON FUNCTION public.classroom_web_attempt_submitted(uuid,uuid) FROM PUBLIC,
  classroom_web_runtime,classroom_web_scheduler,classroom_web_identity;
GRANT EXECUTE ON FUNCTION public.classroom_web_attempt_submitted(uuid,uuid)
  TO classroom_web_task_dispatcher;
REVOKE ALL ON FUNCTION public.classroom_web_local_reconcile_candidates(integer) FROM PUBLIC,
  classroom_web_scheduler,classroom_web_task_dispatcher,classroom_web_identity;
GRANT EXECUTE ON FUNCTION public.classroom_web_local_reconcile_candidates(integer)
  TO classroom_web_runtime;
REVOKE ALL ON FUNCTION public.classroom_web_reconcile_saved_result(uuid,bigint) FROM PUBLIC,
  classroom_web_scheduler,classroom_web_task_dispatcher,classroom_web_identity;
GRANT EXECUTE ON FUNCTION public.classroom_web_reconcile_saved_result(uuid,bigint)
  TO classroom_web_runtime;
GRANT EXECUTE ON FUNCTION public.classroom_web_local_json_payload(text),
  public.classroom_web_local_json_token(text), public.classroom_web_local_float_token(text)
  TO classroom_web_local_result_processor;

ALTER FUNCTION public.classroom_web_identity_usable(uuid)
  OWNER TO classroom_web_identity_checker;
ALTER FUNCTION public.classroom_web_attempt_submitted(uuid,uuid)
  OWNER TO classroom_web_recovery_metadata;
ALTER FUNCTION public.classroom_web_local_reconcile_candidates(integer)
  OWNER TO classroom_web_recovery_metadata;
ALTER FUNCTION public.classroom_web_reconcile_saved_result(uuid,bigint)
  OWNER TO classroom_web_local_result_processor;

DO $wrapper$
DECLARE wrapper_owner text;
        migration_actor text := current_user;
BEGIN
  SELECT r.rolname INTO wrapper_owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
    WHERE p.oid='public.classroom_web_owner_active()'::regprocedure;
  IF wrapper_owner IS NULL OR wrapper_owner IN ('classroom_web_runtime',
      'classroom_web_scheduler','classroom_web_identity','classroom_web_task_dispatcher') THEN
    RAISE EXCEPTION 'identity wrapper requires an independently managed owner';
  END IF;
  -- ALTER OWNER rewrites ACL references to the previous owner. Grant this
  -- dependency afterwards, using only the temporary SET capability, and then
  -- restore the exact DDL identity (which may itself be a provisioned role).
  SET LOCAL ROLE classroom_web_identity_checker;
  EXECUTE format('GRANT EXECUTE ON FUNCTION public.classroom_web_identity_usable(uuid) TO %I',wrapper_owner);
  EXECUTE format('SET LOCAL ROLE %I',migration_actor);
END
$wrapper$;

DO $cleanup$
DECLARE candidate text;
        role_oid oid;
BEGIN
  FOREACH candidate IN ARRAY ARRAY['classroom_web_identity_checker',
      'classroom_web_recovery_metadata','classroom_web_local_result_processor'] LOOP
    EXECUTE format('REVOKE CREATE ON SCHEMA public FROM %I',candidate);
    SELECT oid INTO role_oid FROM pg_roles WHERE rolname=candidate;
    IF EXISTS (SELECT FROM pg_auth_members WHERE roleid=role_oid
        AND member=current_user::regrole) THEN
      EXECUTE format('REVOKE %I FROM %I',candidate,current_user);
    END IF;
    IF EXISTS (SELECT FROM pg_auth_members WHERE roleid=role_oid OR member=role_oid)
      OR EXISTS (SELECT FROM pg_roles WHERE oid=role_oid AND
        (rolsuper OR rolbypassrls OR rolcanlogin OR rolinherit OR rolcreatedb
          OR rolcreaterole OR rolreplication OR rolconfig IS NOT NULL))
      OR has_schema_privilege(candidate,'public','CREATE')
      OR EXISTS (SELECT FROM pg_class WHERE relowner=role_oid)
      OR EXISTS (SELECT FROM pg_namespace WHERE nspowner=role_oid)
      OR EXISTS (SELECT FROM pg_database WHERE datdba=role_oid) THEN
      RAISE EXCEPTION 'local reconciliation function owner cleanup failed';
    END IF;
  END LOOP;
END
$cleanup$;
