-- No root installation or self-claim function exists in this migration.
-- An external installation/maintenance adapter supplies already approved D0.
GRANT USAGE ON SCHEMA identity_control TO classroom_web_identity_table_owner;
DO $roles$
DECLARE n text;lock_column text;
BEGIN
  FOREACH n IN ARRAY ARRAY['classroom_web_manual_recovery_writer','classroom_web_manual_status_reader','classroom_web_registration_maintainer','classroom_web_identity_notice_writer','classroom_web_auth_mail_writer'] LOOP
    IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname=n) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION',n);
    ELSIF EXISTS(SELECT FROM pg_roles WHERE rolname=n AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication OR rolinherit)) THEN RAISE EXCEPTION 'unsafe control owner';END IF;
    IF EXISTS(SELECT FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=n)) THEN RAISE EXCEPTION 'control owner inherits another role';END IF;
  END LOOP;
END $roles$;

ALTER TABLE public.email_actions ADD CONSTRAINT email_action_owner_pair UNIQUE(owner_id,id);
CREATE TABLE identity_control.authentication_mail_jobs(
  id uuid PRIMARY KEY,owner_id uuid,action_id uuid,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),expires_at timestamptz NOT NULL,
  state text NOT NULL DEFAULT 'queued' CHECK(state IN('queued','sending','sent','discarded')),attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
  encrypted_payload text CHECK(encrypted_payload IS NULL OR char_length(encrypted_payload) BETWEEN 1 AND 8192),claim_hash text,claim_until timestamptz,finished_at timestamptz,
  CHECK((owner_id IS NULL)=(action_id IS NULL)),FOREIGN KEY(owner_id,action_id) REFERENCES public.email_actions(owner_id,id),
  CHECK((state IN('sent','discarded'))=(finished_at IS NOT NULL)),CHECK((state='sending')=(claim_until IS NOT NULL)),CHECK(state NOT IN('sent','discarded') OR encrypted_payload IS NULL)
);
ALTER TABLE identity_control.authentication_mail_jobs OWNER TO classroom_web_identity_table_owner;
ALTER TABLE identity_control.authentication_mail_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity_control.authentication_mail_jobs FORCE ROW LEVEL SECURITY;
REVOKE ALL ON identity_control.authentication_mail_jobs FROM PUBLIC;
GRANT USAGE ON SCHEMA identity_api,identity_control,public TO classroom_web_auth_mail_writer;
GRANT SELECT,INSERT ON identity_control.authentication_mail_jobs TO classroom_web_session_writer;
CREATE POLICY mail_enqueue ON identity_control.authentication_mail_jobs TO classroom_web_session_writer USING(true) WITH CHECK(true);
GRANT SELECT,UPDATE(state,attempts,encrypted_payload,claim_hash,claim_until,finished_at) ON identity_control.authentication_mail_jobs TO classroom_web_auth_mail_writer;
CREATE POLICY mail_delivery ON identity_control.authentication_mail_jobs TO classroom_web_auth_mail_writer USING(true) WITH CHECK(true);
GRANT SELECT(id,state,deleted_at,email_verified_at,security_epoch),UPDATE(id) ON public.users TO classroom_web_auth_mail_writer;
CREATE POLICY mail_user_lock ON public.users TO classroom_web_auth_mail_writer USING(true) WITH CHECK(false);
GRANT SELECT(id,owner_id,purpose,security_epoch,expires_at,consumed_at),UPDATE(id) ON public.email_actions TO classroom_web_auth_mail_writer;
CREATE POLICY mail_action_read ON public.email_actions TO classroom_web_auth_mail_writer USING(true) WITH CHECK(false);

CREATE TABLE identity_control.identity_deployment_guards(
  deployment_id uuid PRIMARY KEY,version bigint NOT NULL CHECK(version>=1),manifest_hash text NOT NULL CHECK(manifest_hash~'^[0-9a-f]{64}$'),
  state text NOT NULL CHECK(state IN('closed','applying','ready')),transition_id uuid,broker_instance_id uuid
);
CREATE TABLE identity_control.recovery_authorities(
  id uuid PRIMARY KEY,deployment_id uuid NOT NULL REFERENCES identity_control.identity_deployment_guards(deployment_id),version bigint NOT NULL,
  manifest_hash text NOT NULL,os_mapping_hash text NOT NULL,enabled boolean NOT NULL,expires_at timestamptz NOT NULL,UNIQUE(deployment_id,id)
);
CREATE TABLE identity_control.recovery_permissions(
  deployment_id uuid NOT NULL REFERENCES identity_control.identity_deployment_guards(deployment_id),actor_id uuid NOT NULL REFERENCES public.users(id),
  enabled boolean NOT NULL,expires_at timestamptz NOT NULL,version bigint NOT NULL,PRIMARY KEY(deployment_id,actor_id)
);
CREATE TABLE identity_control.registration_transitions(
  id uuid PRIMARY KEY,deployment_id uuid NOT NULL REFERENCES identity_control.identity_deployment_guards(deployment_id),
  old_version bigint NOT NULL,new_version bigint NOT NULL CHECK(new_version>old_version),old_manifest_hash text NOT NULL,new_manifest_hash text NOT NULL,
  approval_hash text NOT NULL,nonce_hash text UNIQUE NOT NULL,committed_at timestamptz NOT NULL,audit_id uuid UNIQUE NOT NULL REFERENCES identity_control.identity_control_audit(id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE identity_control.registry_revocation_notices(
  id uuid PRIMARY KEY,deployment_id uuid NOT NULL REFERENCES identity_control.identity_deployment_guards(deployment_id),version bigint NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),delivered_at timestamptz
);
CREATE TABLE identity_control.recovery_receivers(
  id uuid PRIMARY KEY,deployment_id uuid NOT NULL REFERENCES identity_control.identity_deployment_guards(deployment_id),public_key jsonb NOT NULL,
  key_hash text NOT NULL CHECK(key_hash~'^[0-9a-f]{64}$'),nonce_hash text NOT NULL CHECK(nonce_hash~'^[0-9a-f]{64}$'),expires_at timestamptz NOT NULL,
  UNIQUE(deployment_id,id),CHECK(jsonb_typeof(public_key)='object' AND pg_column_size(public_key)<=1024)
);
CREATE TABLE identity_control.manual_recovery_cases(
  id uuid PRIMARY KEY,deployment_id uuid NOT NULL REFERENCES identity_control.identity_deployment_guards(deployment_id),
  target_user_id uuid NOT NULL REFERENCES public.users(id),target_role text NOT NULL CHECK(target_role IN('student','admin')),target_epoch bigint NOT NULL,
  executor_type text NOT NULL CHECK(executor_type IN('actor','authority')),executor_id uuid NOT NULL,
  executor_actor_user_id uuid REFERENCES public.users(id),executor_authority_id uuid,
  verification_record_hash text NOT NULL CHECK(verification_record_hash~'^[0-9a-f]{64}$'),
  state text NOT NULL CHECK(state IN('open','pending','pending_confirmed','completed','abandoned')),flow_generation bigint NOT NULL DEFAULT 0 CHECK(flow_generation>=0),
  receiver_id uuid,pending_flow_expiry timestamptz,pending_factor_id uuid,confirmation_hash text,completed_op_id uuid,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),verification_deadline timestamptz NOT NULL,
  issuer_session_hash text,issued_version bigint,issued_manifest_hash text,
  FOREIGN KEY(deployment_id,executor_authority_id) REFERENCES identity_control.recovery_authorities(deployment_id,id),
  FOREIGN KEY(deployment_id,receiver_id) REFERENCES identity_control.recovery_receivers(deployment_id,id),
  FOREIGN KEY(target_user_id,pending_factor_id) REFERENCES public.mfa_credentials(owner_id,id) DEFERRABLE INITIALLY DEFERRED,
  UNIQUE(deployment_id,id),UNIQUE(deployment_id,id,target_user_id),UNIQUE(deployment_id,id,executor_type,executor_id),
  CHECK((executor_type='actor' AND executor_actor_user_id IS NOT NULL AND executor_actor_user_id=executor_id AND executor_authority_id IS NULL) OR (executor_type='authority' AND executor_authority_id IS NOT NULL AND executor_authority_id=executor_id AND executor_actor_user_id IS NULL)),
  CHECK((executor_type='actor' AND target_role='student') OR (executor_type='authority' AND target_role='admin')),
  CHECK((state='completed')=(completed_op_id IS NOT NULL)),
  CHECK(state NOT IN('pending','pending_confirmed') OR pending_flow_expiry IS NOT NULL),
  CHECK(state<>'pending_confirmed' OR (pending_factor_id IS NOT NULL AND confirmation_hash IS NOT NULL))
);
ALTER TABLE public.mfa_credentials ADD COLUMN manual_deployment_id uuid;
ALTER TABLE public.mfa_credentials ADD COLUMN manual_case_id uuid;
ALTER TABLE public.mfa_credentials ADD CONSTRAINT mfa_manual_case_owner FOREIGN KEY(manual_deployment_id,manual_case_id,owner_id)
  REFERENCES identity_control.manual_recovery_cases(deployment_id,id,target_user_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public.mfa_credentials ADD CONSTRAINT mfa_manual_case_pair CHECK((manual_deployment_id IS NULL)=(manual_case_id IS NULL));
CREATE TABLE identity_control.manual_recovery_operations(
  id uuid PRIMARY KEY,deployment_id uuid NOT NULL,case_id uuid NOT NULL,executor_type text NOT NULL,executor_id uuid NOT NULL,
  action text NOT NULL CHECK(action IN('issue','reissue','complete')),plan_hash text NOT NULL CHECK(plan_hash~'^[0-9a-f]{64}$'),
  state text NOT NULL CHECK(state IN('reserved','committed')),audit_id uuid REFERENCES identity_control.identity_control_audit(id) DEFERRABLE INITIALLY DEFERRED,committed_at timestamptz,
  FOREIGN KEY(deployment_id,case_id,executor_type,executor_id) REFERENCES identity_control.manual_recovery_cases(deployment_id,id,executor_type,executor_id),
  UNIQUE(deployment_id,case_id,id),UNIQUE(deployment_id,case_id,id,executor_type,executor_id,action,plan_hash),
  CHECK((state='committed')=(audit_id IS NOT NULL AND committed_at IS NOT NULL))
);
ALTER TABLE identity_control.manual_recovery_cases ADD CONSTRAINT case_completed_operation FOREIGN KEY(deployment_id,id,completed_op_id)
  REFERENCES identity_control.manual_recovery_operations(deployment_id,case_id,id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE identity_control.identity_operation_proofs(
  id uuid PRIMARY KEY,deployment_id uuid NOT NULL,case_id uuid NOT NULL,op_id uuid NOT NULL,
  executor_type text NOT NULL,executor_id uuid NOT NULL,action text NOT NULL,plan_hash text NOT NULL CHECK(plan_hash~'^[0-9a-f]{64}$'),nonce_hash text UNIQUE NOT NULL CHECK(nonce_hash~'^[0-9a-f]{64}$'),
  version bigint NOT NULL,manifest_hash text NOT NULL,verified_at timestamptz NOT NULL,challenge_expiry timestamptz NOT NULL,operation_deadline timestamptz NOT NULL,
  consumed_at timestamptz,scope jsonb NOT NULL CHECK(jsonb_typeof(scope)='object' AND pg_column_size(scope)<=8192),
  FOREIGN KEY(deployment_id,case_id,op_id) REFERENCES identity_control.manual_recovery_operations(deployment_id,case_id,id),
  FOREIGN KEY(deployment_id,case_id,executor_type,executor_id) REFERENCES identity_control.manual_recovery_cases(deployment_id,id,executor_type,executor_id),
  FOREIGN KEY(deployment_id,case_id,op_id,executor_type,executor_id,action,plan_hash) REFERENCES identity_control.manual_recovery_operations(deployment_id,case_id,id,executor_type,executor_id,action,plan_hash),
  UNIQUE(deployment_id,case_id,id),
  CHECK(verified_at<challenge_expiry AND operation_deadline>verified_at AND operation_deadline<=verified_at+interval '120 seconds')
);
CREATE TABLE identity_control.manual_recovery_capabilities(
  id uuid PRIMARY KEY,deployment_id uuid NOT NULL,case_id uuid NOT NULL,flow_generation bigint NOT NULL CHECK(flow_generation>=1),receiver_id uuid NOT NULL,
  token_hash text UNIQUE NOT NULL CHECK(token_hash~'^[0-9a-f]{64}$'),issue_proof_id uuid NOT NULL REFERENCES identity_control.identity_operation_proofs(id),
  expires_at timestamptz NOT NULL,consumed_at timestamptz,
  FOREIGN KEY(deployment_id,case_id) REFERENCES identity_control.manual_recovery_cases(deployment_id,id),
  FOREIGN KEY(deployment_id,case_id,issue_proof_id) REFERENCES identity_control.identity_operation_proofs(deployment_id,case_id,id),
  FOREIGN KEY(deployment_id,receiver_id) REFERENCES identity_control.recovery_receivers(deployment_id,id)
);
ALTER TABLE identity_control.identity_control_audit ADD COLUMN deployment_id uuid;
ALTER TABLE identity_control.identity_control_audit ADD COLUMN case_id uuid;
ALTER TABLE identity_control.identity_control_audit ADD COLUMN op_id uuid;
ALTER TABLE identity_control.identity_control_audit ADD COLUMN authority_id uuid;
ALTER TABLE identity_control.identity_control_audit ADD CONSTRAINT audit_manual_operation FOREIGN KEY(deployment_id,case_id,op_id)
  REFERENCES identity_control.manual_recovery_operations(deployment_id,case_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE identity_control.identity_control_audit ADD CONSTRAINT audit_authority FOREIGN KEY(deployment_id,authority_id) REFERENCES identity_control.recovery_authorities(deployment_id,id);
ALTER TABLE identity_control.identity_control_audit ADD CONSTRAINT audit_scope UNIQUE(id,deployment_id,case_id,op_id);
ALTER TABLE identity_control.manual_recovery_operations ADD CONSTRAINT operation_exact_audit FOREIGN KEY(audit_id,deployment_id,case_id,id)
  REFERENCES identity_control.identity_control_audit(id,deployment_id,case_id,op_id) DEFERRABLE INITIALLY DEFERRED;

GRANT USAGE ON SCHEMA public,identity_control,identity_api TO classroom_web_manual_recovery_writer,classroom_web_manual_status_reader,classroom_web_registration_maintainer;
GRANT SELECT(id,role,state,deleted_at,email_verified_at,password_hash,security_epoch,revision) ON public.users TO classroom_web_manual_recovery_writer;
GRANT UPDATE(id,security_epoch,revision,updated_at) ON public.users TO classroom_web_manual_recovery_writer;
GRANT SELECT(id,role,state,deleted_at,email_verified_at,security_epoch) ON public.users TO classroom_web_manual_status_reader;
GRANT UPDATE(id) ON public.users TO classroom_web_manual_status_reader;
CREATE POLICY manual_write_users ON public.users TO classroom_web_manual_recovery_writer USING(true) WITH CHECK(true);
CREATE POLICY manual_status_users ON public.users TO classroom_web_manual_status_reader USING(true) WITH CHECK(false);

DO $tables$
DECLARE n text;lock_column text;
BEGIN
  FOREACH n IN ARRAY ARRAY['identity_deployment_guards','recovery_authorities','recovery_permissions','recovery_receivers','manual_recovery_cases','manual_recovery_operations','identity_operation_proofs','manual_recovery_capabilities','registration_transitions','registry_revocation_notices'] LOOP
    EXECUTE format('ALTER TABLE identity_control.%I OWNER TO classroom_web_identity_table_owner',n);
    EXECUTE format('ALTER TABLE identity_control.%I ENABLE ROW LEVEL SECURITY',n);
    EXECUTE format('ALTER TABLE identity_control.%I FORCE ROW LEVEL SECURITY',n);
    EXECUTE format('REVOKE ALL ON identity_control.%I FROM PUBLIC',n);
    IF n IN('identity_deployment_guards','recovery_authorities','recovery_permissions') THEN
      lock_column:=CASE n WHEN 'identity_deployment_guards' THEN 'deployment_id' WHEN 'recovery_permissions' THEN 'actor_id' ELSE 'id' END;
      EXECUTE format('GRANT SELECT,UPDATE(%I) ON identity_control.%I TO classroom_web_manual_recovery_writer',lock_column,n);
      EXECUTE format('CREATE POLICY manual_control ON identity_control.%I TO classroom_web_manual_recovery_writer USING(true) WITH CHECK(false)',n);
      EXECUTE format('GRANT SELECT,INSERT,UPDATE ON identity_control.%I TO classroom_web_registration_maintainer',n);
      EXECUTE format('CREATE POLICY registry_maintenance ON identity_control.%I TO classroom_web_registration_maintainer USING(true) WITH CHECK(true)',n);
    ELSIF n IN('registration_transitions','registry_revocation_notices') THEN
      EXECUTE format('GRANT SELECT,INSERT ON identity_control.%I TO classroom_web_registration_maintainer',n);
      EXECUTE format('CREATE POLICY registry_event_append ON identity_control.%I TO classroom_web_registration_maintainer USING(true) WITH CHECK(true)',n);
    ELSE
      EXECUTE format('GRANT SELECT,INSERT,UPDATE ON identity_control.%I TO classroom_web_manual_recovery_writer',n);
      EXECUTE format('CREATE POLICY manual_control ON identity_control.%I TO classroom_web_manual_recovery_writer USING(true) WITH CHECK(true)',n);
    END IF;
  END LOOP;
  FOREACH n IN ARRAY ARRAY['identity_deployment_guards','recovery_authorities','recovery_permissions','manual_recovery_cases','manual_recovery_operations'] LOOP
    IF n NOT IN('manual_recovery_cases','manual_recovery_operations') THEN EXECUTE format('GRANT SELECT ON identity_control.%I TO classroom_web_manual_status_reader',n);END IF;
    lock_column:=CASE n WHEN 'identity_deployment_guards' THEN 'deployment_id' WHEN 'recovery_permissions' THEN 'actor_id' ELSE 'id' END;
    EXECUTE format('GRANT UPDATE(%I) ON identity_control.%I TO classroom_web_manual_status_reader',lock_column,n);
    EXECUTE format('CREATE POLICY manual_status_select ON identity_control.%I FOR SELECT TO classroom_web_manual_status_reader USING(true)',n);
    EXECUTE format('CREATE POLICY manual_status_lock ON identity_control.%I FOR UPDATE TO classroom_web_manual_status_reader USING(true) WITH CHECK(false)',n);
  END LOOP;
END $tables$;
GRANT SELECT(id,deployment_id,executor_type,executor_id,state,flow_generation,pending_flow_expiry) ON identity_control.manual_recovery_cases TO classroom_web_manual_status_reader;
GRANT SELECT(id,deployment_id,case_id,executor_type,executor_id,action,state,audit_id,committed_at) ON identity_control.manual_recovery_operations TO classroom_web_manual_status_reader;

GRANT SELECT,INSERT,UPDATE ON public.mfa_credentials TO classroom_web_manual_recovery_writer;
GRANT SELECT,UPDATE(revoked_at) ON public.auth_sessions,public.recovery_code_groups TO classroom_web_manual_recovery_writer;
GRANT SELECT,UPDATE(consumed_at) ON public.auth_challenges,public.reauth_proofs,public.email_actions TO classroom_web_manual_recovery_writer;
GRANT SELECT ON public.devices TO classroom_web_manual_recovery_writer;
GRANT INSERT ON identity_control.identity_control_audit,identity_control.identity_revocation_notices TO classroom_web_manual_recovery_writer,classroom_web_registration_maintainer;
GRANT SELECT(id,created_at,deployment_id,case_id,op_id,action,authority_id,actor_id,subject_id) ON identity_control.identity_control_audit TO classroom_web_manual_status_reader;
CREATE POLICY manual_audit_append ON identity_control.identity_control_audit TO classroom_web_manual_recovery_writer,classroom_web_registration_maintainer USING(false) WITH CHECK(true);
CREATE POLICY manual_audit_read ON identity_control.identity_control_audit FOR SELECT TO classroom_web_manual_status_reader USING(true);
CREATE POLICY manual_notice_append ON identity_control.identity_revocation_notices TO classroom_web_manual_recovery_writer,classroom_web_registration_maintainer USING(false) WITH CHECK(true);
CREATE POLICY manual_factor_control ON public.mfa_credentials TO classroom_web_manual_recovery_writer USING(true) WITH CHECK(true);
CREATE POLICY manual_session_control ON public.auth_sessions TO classroom_web_manual_recovery_writer USING(true) WITH CHECK(true);
CREATE POLICY manual_group_control ON public.recovery_code_groups TO classroom_web_manual_recovery_writer USING(true) WITH CHECK(true);
CREATE POLICY manual_challenge_control ON public.auth_challenges TO classroom_web_manual_recovery_writer USING(true) WITH CHECK(true);
CREATE POLICY manual_reauth_control ON public.reauth_proofs TO classroom_web_manual_recovery_writer USING(true) WITH CHECK(true);
CREATE POLICY manual_action_control ON public.email_actions TO classroom_web_manual_recovery_writer USING(true) WITH CHECK(true);
CREATE POLICY manual_device_read ON public.devices FOR SELECT TO classroom_web_manual_recovery_writer USING(true);
GRANT UPDATE(id) ON public.devices TO classroom_web_manual_recovery_writer;
GRANT EXECUTE ON FUNCTION identity_api.validate_session(jsonb),identity_api.revoke_derived(uuid) TO classroom_web_manual_recovery_writer;
GRANT INSERT ON public.reauth_proofs TO classroom_web_manual_recovery_writer;
ALTER TABLE public.reauth_proofs ADD COLUMN control_deployment_id uuid;
ALTER TABLE public.reauth_proofs ADD COLUMN control_op_id uuid;
ALTER TABLE public.reauth_proofs ADD COLUMN control_plan_hash text;
ALTER TABLE public.reauth_proofs ADD CONSTRAINT control_reauth_scope CHECK(
  (control_deployment_id IS NULL AND control_op_id IS NULL AND control_plan_hash IS NULL)
  OR (control_deployment_id IS NOT NULL AND control_op_id IS NOT NULL AND control_plan_hash~'^[0-9a-f]{64}$' AND purpose LIKE 'student_recovery_%'));

CREATE FUNCTION identity_api.control_guard(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE g identity_control.identity_deployment_guards%ROWTYPE;
BEGIN
  SELECT * INTO g FROM identity_control.identity_deployment_guards WHERE deployment_id=(p->>'deployment_id')::uuid FOR SHARE;
  IF g.deployment_id IS NULL OR g.state<>'ready' OR g.version IS DISTINCT FROM (p->>'version')::bigint OR g.manifest_hash IS DISTINCT FROM p->>'manifest_hash'
    OR p->>'broker_instance_id' IS NULL OR g.broker_instance_id IS DISTINCT FROM (p->>'broker_instance_id')::uuid THEN RETURN NULL;END IF;
  RETURN jsonb_build_object('deployment_id',g.deployment_id,'version',g.version,'manifest_hash',g.manifest_hash);
END $f$;

-- Repeated at the final migration boundary after every new function exists.
-- No unreviewed public/default EXECUTE survives a migration commit.
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA identity_api FROM PUBLIC;

CREATE FUNCTION identity_api.control_executor(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE s jsonb;a identity_control.recovery_authorities%ROWTYPE;permission identity_control.recovery_permissions%ROWTYPE;n timestamptz;
BEGIN
  IF identity_api.control_guard(p) IS NULL THEN RETURN NULL;END IF;
  IF p->>'auth_context_deadline' IS NULL OR clock_timestamp()>=(p->>'auth_context_deadline')::timestamptz THEN RETURN NULL;END IF;
  IF p->>'executor_type'='authority' THEN
    SELECT * INTO a FROM identity_control.recovery_authorities WHERE deployment_id=(p->>'deployment_id')::uuid AND id=(p->>'executor_id')::uuid FOR SHARE;
    n:=clock_timestamp();
    IF a.id IS NULL OR NOT a.enabled OR n>=a.expires_at OR a.version<>(p->>'version')::bigint OR a.manifest_hash IS DISTINCT FROM p->>'manifest_hash'
      OR a.os_mapping_hash IS DISTINCT FROM p->>'os_mapping_hash' THEN RETURN NULL;END IF;
    RETURN jsonb_build_object('executor_type','authority','executor_id',a.id,'expires_at',least(a.expires_at,(p->>'auth_context_deadline')::timestamptz));
  ELSIF p->>'executor_type'='actor' THEN
    s:=identity_api.validate_session(jsonb_build_object('token_hash',p->>'actor_session_hash','touch',false));
    IF s IS NULL OR s->>'role'<>'admin' OR s->>'mfa_level'<>'totp' OR s->>'owner_id' IS DISTINCT FROM p->>'executor_id' THEN RETURN NULL;END IF;
    SELECT * INTO permission FROM identity_control.recovery_permissions WHERE deployment_id=(p->>'deployment_id')::uuid AND actor_id=(s->>'owner_id')::uuid FOR SHARE;
    n:=clock_timestamp();
    IF permission.actor_id IS NULL OR NOT permission.enabled OR permission.version<>(p->>'version')::bigint OR n>=permission.expires_at THEN RETURN NULL;END IF;
    RETURN jsonb_build_object('executor_type','actor','executor_id',s->>'owner_id','expires_at',least(permission.expires_at,(s->>'expires_at')::timestamptz,(p->>'auth_context_deadline')::timestamptz),'session_id',s->>'session_id','device_id',s->>'device_id','epoch',s->>'epoch');
  ELSE RETURN NULL;END IF;
END $f$;

CREATE FUNCTION identity_api.open_manual_case(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE target record;context jsonb;
BEGIN
  IF identity_api.control_guard(p) IS NULL THEN RETURN NULL;END IF;
  -- Acquire all affected user locks in canonical order, including FK parents.
  PERFORM id FROM public.users WHERE id IN((p->>'target_user_id')::uuid,CASE WHEN p->>'executor_type'='actor' THEN (p->>'executor_id')::uuid ELSE NULL END) ORDER BY id FOR UPDATE;
  context:=identity_api.control_executor(p);IF context IS NULL THEN RETURN NULL;END IF;
  SELECT id,role,state,deleted_at,security_epoch INTO target FROM public.users WHERE id=(p->>'target_user_id')::uuid;
  IF target.id IS NULL OR target.state<>'active' OR target.deleted_at IS NOT NULL OR (p->>'executor_type'='actor' AND target.role<>'student') OR (p->>'executor_type'='authority' AND target.role<>'admin')
    OR target.role IS DISTINCT FROM p->>'verified_target_role' OR target.security_epoch IS DISTINCT FROM (p->>'verified_target_epoch')::bigint
    OR p->>'verification_deadline' IS NULL OR clock_timestamp()>=(p->>'verification_deadline')::timestamptz THEN RETURN NULL;END IF;
  INSERT INTO identity_control.manual_recovery_cases(id,deployment_id,target_user_id,target_role,target_epoch,executor_type,executor_id,executor_actor_user_id,executor_authority_id,verification_record_hash,state,verification_deadline,issuer_session_hash)
    VALUES((p->>'case_id')::uuid,(p->>'deployment_id')::uuid,target.id,target.role,target.security_epoch,p->>'executor_type',(p->>'executor_id')::uuid,
      CASE WHEN p->>'executor_type'='actor' THEN (p->>'executor_id')::uuid END,CASE WHEN p->>'executor_type'='authority' THEN (p->>'executor_id')::uuid END,p->>'verification_record_hash','open',(p->>'verification_deadline')::timestamptz,p->>'actor_session_hash');
  RETURN jsonb_build_object('case_id',p->>'case_id','stage','open','target_role',target.role,'target_security_epoch',target.security_epoch,'verification_record_hash',p->>'verification_record_hash');
END $f$;

CREATE FUNCTION identity_api.manual_case_plan(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE c identity_control.manual_recovery_cases%ROWTYPE;r identity_control.recovery_receivers%ROWTYPE;context jsonb;target record;
BEGIN
  IF identity_api.control_guard(p) IS NULL THEN RETURN NULL;END IF;
  SELECT * INTO c FROM identity_control.manual_recovery_cases WHERE deployment_id=(p->>'deployment_id')::uuid AND id=(p->>'case_id')::uuid;
  IF c.id IS NULL THEN RETURN NULL;END IF;
  PERFORM id FROM public.users WHERE id IN(c.target_user_id,CASE WHEN c.executor_type='actor' THEN c.executor_id ELSE NULL END) ORDER BY id FOR UPDATE;
  context:=identity_api.control_executor(p);IF context IS NULL OR c.executor_type IS DISTINCT FROM p->>'executor_type' OR c.executor_id IS DISTINCT FROM (p->>'executor_id')::uuid THEN RETURN NULL;END IF;
  SELECT * INTO c FROM identity_control.manual_recovery_cases WHERE id=c.id FOR UPDATE;
  SELECT role,security_epoch,state,deleted_at INTO target FROM public.users WHERE id=c.target_user_id;
  IF target.role<>c.target_role OR target.security_epoch<>c.target_epoch OR target.state<>'active' OR target.deleted_at IS NOT NULL OR c.state IN('completed','abandoned') OR clock_timestamp()>=c.verification_deadline THEN RETURN NULL;END IF;
  IF p->>'action'='issue' AND c.state<>'open' THEN RETURN NULL;END IF;
  IF p->>'action'='reissue' AND (c.state NOT IN('pending','pending_confirmed') OR clock_timestamp()>=c.pending_flow_expiry) THEN RETURN NULL;END IF;
  IF p->>'action'='complete' AND (c.state<>'pending_confirmed' OR clock_timestamp()>=c.pending_flow_expiry) THEN RETURN NULL;END IF;
  SELECT * INTO r FROM identity_control.recovery_receivers WHERE deployment_id=c.deployment_id AND id=coalesce((p->>'receiver_id')::uuid,c.receiver_id) FOR SHARE;
  IF r.id IS NULL OR (p->>'action'<>'complete' AND clock_timestamp()>=r.expires_at) OR (p->>'action'='complete' AND r.id<>c.receiver_id) THEN RETURN NULL;END IF;
  RETURN jsonb_build_object('deployment_id',c.deployment_id,'case_id',c.id,'op_id',p->>'op_id','action',p->>'action',
    'executor',jsonb_build_object('executor_type',c.executor_type,'executor_id',c.executor_id),'authority_version',CASE WHEN c.executor_type='authority' THEN (p->>'version')::bigint ELSE NULL END,
    'target_user_id',c.target_user_id,'target_role',c.target_role,'target_security_epoch',c.target_epoch,'verification_record_hash',c.verification_record_hash,
    'receiver_id',r.id,'receiver_key_hash',r.key_hash,'flow_generation',CASE WHEN p->>'action'='complete' THEN c.flow_generation ELSE c.flow_generation+1 END,
    'pending_factor_id',CASE WHEN p->>'action'='complete' THEN c.pending_factor_id ELSE NULL END,'confirmation_hash',CASE WHEN p->>'action'='complete' THEN c.confirmation_hash ELSE NULL END);
END $f$;

CREATE FUNCTION identity_api.register_receiver(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
BEGIN
  IF identity_api.control_guard(p) IS NULL THEN RETURN NULL;END IF;
  IF jsonb_typeof(p->'public_key') IS DISTINCT FROM 'object' THEN RETURN NULL;END IF;
  INSERT INTO identity_control.recovery_receivers(id,deployment_id,public_key,key_hash,nonce_hash,expires_at)
    VALUES((p->>'receiver_id')::uuid,(p->>'deployment_id')::uuid,p->'public_key',p->>'key_hash',p->>'nonce_hash',clock_timestamp()+interval '5 minutes');
  RETURN jsonb_build_object('expires_at',clock_timestamp()+interval '5 minutes');
END $f$;

CREATE FUNCTION identity_api.prepare_manual_proof(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE plan jsonb;context jsonb;c identity_control.manual_recovery_cases%ROWTYPE;op identity_control.manual_recovery_operations%ROWTYPE;bridge public.reauth_proofs%ROWTYPE;n timestamptz;verified timestamptz:=(p->>'verified_at')::timestamptz;deadline timestamptz;
BEGIN
  plan:=identity_api.manual_case_plan(p);IF plan IS NULL OR plan IS DISTINCT FROM p->'plan' THEN RETURN NULL;END IF;
  SELECT * INTO c FROM identity_control.manual_recovery_cases WHERE id=(p->>'case_id')::uuid;
  IF c.executor_type='actor' THEN
    SELECT * INTO bridge FROM public.reauth_proofs WHERE token_hash=p->>'actor_bridge_hash' AND owner_id=c.executor_id FOR UPDATE;
    IF bridge.id IS NULL OR bridge.consumed_at IS NOT NULL OR bridge.control_deployment_id IS DISTINCT FROM c.deployment_id OR bridge.target_id IS DISTINCT FROM c.id
      OR bridge.control_op_id IS DISTINCT FROM (p->>'op_id')::uuid OR bridge.control_plan_hash IS DISTINCT FROM p->>'plan_hash' OR bridge.purpose IS DISTINCT FROM 'student_recovery_'||(p->>'action')
      OR clock_timestamp()>=bridge.expires_at OR verified IS DISTINCT FROM bridge.verified_at THEN RETURN NULL;END IF;
    verified:=bridge.verified_at;
  END IF;
  n:=clock_timestamp();
  IF verified IS NULL OR verified>n OR p->>'challenge_issued_at' IS NULL OR verified<(p->>'challenge_issued_at')::timestamptz
    OR verified>=(p->>'challenge_expiry')::timestamptz OR n>=verified+interval '120 seconds' THEN RETURN NULL;END IF;
  context:=identity_api.control_executor(p);IF context IS NULL THEN RETURN NULL;END IF;
  deadline:=least(verified+interval '120 seconds',c.verification_deadline,coalesce(c.pending_flow_expiry,verified+interval '120 seconds'),(context->>'expires_at')::timestamptz,coalesce(bridge.expires_at,verified+interval '120 seconds'));
  IF n>=deadline THEN RETURN NULL;END IF;
  SELECT * INTO op FROM identity_control.manual_recovery_operations WHERE id=(p->>'op_id')::uuid FOR UPDATE;
  IF op.id IS NOT NULL THEN RETURN NULL;END IF;
  INSERT INTO identity_control.manual_recovery_operations(id,deployment_id,case_id,executor_type,executor_id,action,plan_hash,state)
    VALUES((p->>'op_id')::uuid,c.deployment_id,c.id,c.executor_type,c.executor_id,p->>'action',p->>'plan_hash','reserved');
  INSERT INTO identity_control.identity_operation_proofs(id,deployment_id,case_id,op_id,executor_type,executor_id,action,plan_hash,nonce_hash,version,manifest_hash,verified_at,challenge_expiry,operation_deadline,scope)
    VALUES((p->>'proof_id')::uuid,c.deployment_id,c.id,(p->>'op_id')::uuid,c.executor_type,c.executor_id,p->>'action',p->>'plan_hash',p->>'nonce_hash',(p->>'version')::bigint,p->>'manifest_hash',verified,(p->>'challenge_expiry')::timestamptz,deadline,plan);
  IF bridge.id IS NOT NULL THEN UPDATE public.reauth_proofs SET consumed_at=n WHERE id=bridge.id;END IF;
  RETURN jsonb_build_object('operation_deadline',deadline,'verified_at',verified);
END $f$;

CREATE FUNCTION identity_api.issue_manual(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE c identity_control.manual_recovery_cases%ROWTYPE;proof identity_control.identity_operation_proofs%ROWTYPE;context jsonb;target record;n timestamptz;until_at timestamptz;
BEGIN
  IF identity_api.control_guard(p) IS NULL THEN RETURN NULL;END IF;
  SELECT * INTO c FROM identity_control.manual_recovery_cases WHERE id=(p->>'case_id')::uuid AND deployment_id=(p->>'deployment_id')::uuid;
  IF c.id IS NULL THEN RETURN NULL;END IF;
  PERFORM id FROM public.users WHERE id IN(c.target_user_id,CASE WHEN c.executor_type='actor' THEN c.executor_id ELSE NULL END) ORDER BY id FOR UPDATE;
  context:=identity_api.control_executor(p);IF context IS NULL OR c.executor_type IS DISTINCT FROM p->>'executor_type' OR c.executor_id IS DISTINCT FROM (p->>'executor_id')::uuid THEN RETURN NULL;END IF;
  SELECT * INTO c FROM identity_control.manual_recovery_cases WHERE id=c.id FOR UPDATE;
  PERFORM id FROM identity_control.manual_recovery_operations WHERE id=(p->>'op_id')::uuid AND case_id=c.id FOR UPDATE;
  SELECT role,state,deleted_at,security_epoch INTO target FROM public.users WHERE id=c.target_user_id;
  PERFORM id FROM identity_control.recovery_receivers WHERE id=(p->>'receiver_id')::uuid AND deployment_id=c.deployment_id
    FOR UPDATE;
  IF NOT FOUND THEN RETURN NULL;END IF;
  SELECT * INTO proof FROM identity_control.identity_operation_proofs WHERE id=(p->>'proof_id')::uuid AND case_id=c.id AND op_id=(p->>'op_id')::uuid FOR UPDATE;
  n:=clock_timestamp();
  IF proof.id IS NULL OR proof.action NOT IN('issue','reissue') OR proof.consumed_at IS NOT NULL OR n>=least(proof.operation_deadline,c.verification_deadline,(context->>'expires_at')::timestamptz)
    OR NOT EXISTS(SELECT FROM identity_control.recovery_receivers WHERE id=(p->>'receiver_id')::uuid AND id=(proof.scope->>'receiver_id')::uuid AND key_hash=proof.scope->>'receiver_key_hash' AND n<expires_at)
    OR proof.version<>(p->>'version')::bigint OR proof.manifest_hash IS DISTINCT FROM p->>'manifest_hash' OR proof.plan_hash IS DISTINCT FROM p->>'plan_hash'
    OR target.role<>c.target_role OR target.state<>'active' OR target.deleted_at IS NOT NULL OR target.security_epoch<>c.target_epoch
    OR proof.scope->>'target_user_id' IS DISTINCT FROM c.target_user_id::text OR proof.scope->>'target_role' IS DISTINCT FROM c.target_role
    OR (proof.scope->>'target_security_epoch')::bigint IS DISTINCT FROM c.target_epoch OR (proof.scope->>'flow_generation')::bigint IS DISTINCT FROM c.flow_generation+1
    OR (proof.action='issue' AND c.state<>'open') OR (proof.action='reissue' AND c.state NOT IN('pending','pending_confirmed')) THEN RETURN NULL;END IF;
  until_at:=CASE WHEN proof.action='issue' THEN n+interval '10 minutes' ELSE c.pending_flow_expiry END;
  IF until_at IS NULL OR n>=until_at THEN RETURN NULL;END IF;
  UPDATE identity_control.recovery_receivers SET expires_at=until_at WHERE id=(p->>'receiver_id')::uuid;
  UPDATE identity_control.manual_recovery_capabilities SET consumed_at=n WHERE case_id=c.id AND consumed_at IS NULL;
  UPDATE public.mfa_credentials SET state='revoked',revoked_at=n WHERE owner_id=c.target_user_id AND manual_case_id=c.id AND state='pending';
  UPDATE identity_control.manual_recovery_cases SET state='pending',flow_generation=flow_generation+1,receiver_id=(p->>'receiver_id')::uuid,pending_flow_expiry=until_at,pending_factor_id=NULL,confirmation_hash=NULL,
    issued_version=(p->>'version')::bigint,issued_manifest_hash=p->>'manifest_hash',issuer_session_hash=p->>'actor_session_hash' WHERE id=c.id RETURNING * INTO c;
  INSERT INTO identity_control.manual_recovery_capabilities(id,deployment_id,case_id,flow_generation,receiver_id,token_hash,issue_proof_id,expires_at)
    VALUES((p->>'cap_id')::uuid,c.deployment_id,c.id,c.flow_generation,c.receiver_id,p->>'cap_hash',proof.id,until_at);
  UPDATE identity_control.identity_operation_proofs SET consumed_at=n WHERE id=proof.id;
  INSERT INTO identity_control.identity_control_audit(id,deployment_id,case_id,op_id,authority_id,actor_id,subject_id,action,target_id)
    VALUES((p->>'audit_id')::uuid,c.deployment_id,c.id,proof.op_id,c.executor_authority_id,c.executor_actor_user_id,c.target_user_id,proof.action,c.id);
  UPDATE identity_control.manual_recovery_operations SET state='committed',committed_at=n,audit_id=(p->>'audit_id')::uuid WHERE id=proof.op_id;
  RETURN jsonb_build_object('case_id',c.id,'op_id',proof.op_id,'flow_generation',c.flow_generation,'pending_flow_expiry',until_at,'audit_id',p->>'audit_id','result','committed','stage','pending');
END $f$;

CREATE FUNCTION identity_api.complete_manual(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE c identity_control.manual_recovery_cases%ROWTYPE;proof identity_control.identity_operation_proofs%ROWTYPE;f public.mfa_credentials%ROWTYPE;cap identity_control.manual_recovery_capabilities%ROWTYPE;context jsonb;target record;n timestamptz;
BEGIN
  IF identity_api.control_guard(p) IS NULL THEN RETURN NULL;END IF;
  SELECT * INTO c FROM identity_control.manual_recovery_cases WHERE deployment_id=(p->>'deployment_id')::uuid AND id=(p->>'case_id')::uuid;
  IF c.id IS NULL THEN RETURN NULL;END IF;
  PERFORM id FROM public.users WHERE id IN(c.target_user_id,CASE WHEN c.executor_type='actor' THEN c.executor_id ELSE NULL END) ORDER BY id FOR UPDATE;
  context:=identity_api.control_executor(p);IF context IS NULL OR c.executor_type IS DISTINCT FROM p->>'executor_type' OR c.executor_id IS DISTINCT FROM (p->>'executor_id')::uuid THEN RETURN NULL;END IF;
  SELECT * INTO c FROM identity_control.manual_recovery_cases WHERE id=c.id FOR UPDATE;
  PERFORM id FROM identity_control.manual_recovery_operations WHERE id=(p->>'op_id')::uuid AND case_id=c.id FOR UPDATE;
  PERFORM id FROM identity_control.recovery_receivers WHERE id=c.receiver_id FOR SHARE;
  SELECT * INTO proof FROM identity_control.identity_operation_proofs WHERE id=(p->>'proof_id')::uuid AND case_id=c.id AND op_id=(p->>'op_id')::uuid FOR UPDATE;
  SELECT * INTO cap FROM identity_control.manual_recovery_capabilities WHERE case_id=c.id AND receiver_id=c.receiver_id AND flow_generation=c.flow_generation AND consumed_at IS NULL FOR UPDATE;
  PERFORM id FROM public.mfa_credentials WHERE owner_id=c.target_user_id ORDER BY id FOR UPDATE;
  SELECT * INTO f FROM public.mfa_credentials WHERE owner_id=c.target_user_id AND id=c.pending_factor_id AND manual_deployment_id=c.deployment_id AND manual_case_id=c.id FOR UPDATE;
  PERFORM id FROM public.recovery_code_groups WHERE owner_id=c.target_user_id ORDER BY id FOR UPDATE;
  SELECT role,state,deleted_at,security_epoch INTO target FROM public.users WHERE id=c.target_user_id;n:=clock_timestamp();
  IF c.state<>'pending_confirmed' OR f.id IS NULL OR f.state<>'pending' OR cap.id IS NULL OR proof.id IS NULL OR proof.action<>'complete' OR proof.consumed_at IS NOT NULL
    OR n>=least(proof.operation_deadline,c.verification_deadline,c.pending_flow_expiry,cap.expires_at,(context->>'expires_at')::timestamptz,f.expires_at) OR proof.version<>(p->>'version')::bigint
    OR c.issued_version IS DISTINCT FROM (p->>'version')::bigint OR c.issued_manifest_hash IS DISTINCT FROM p->>'manifest_hash'
    OR proof.manifest_hash IS DISTINCT FROM p->>'manifest_hash' OR proof.plan_hash IS DISTINCT FROM p->>'plan_hash'
    OR target.role<>c.target_role OR target.state<>'active' OR target.deleted_at IS NOT NULL OR target.security_epoch<>c.target_epoch
    OR c.confirmation_hash IS DISTINCT FROM p->>'confirmation_hash' OR c.pending_factor_id IS DISTINCT FROM (p->>'pending_factor_id')::uuid
    OR proof.scope->>'target_user_id' IS DISTINCT FROM c.target_user_id::text OR proof.scope->>'target_role' IS DISTINCT FROM c.target_role
    OR (proof.scope->>'target_security_epoch')::bigint IS DISTINCT FROM c.target_epoch OR (proof.scope->>'flow_generation')::bigint IS DISTINCT FROM c.flow_generation
    OR proof.scope->>'pending_factor_id' IS DISTINCT FROM c.pending_factor_id::text OR proof.scope->>'confirmation_hash' IS DISTINCT FROM c.confirmation_hash
    OR proof.scope->>'receiver_id' IS DISTINCT FROM c.receiver_id::text THEN RETURN NULL;END IF;
  UPDATE public.mfa_credentials SET state='revoked',revoked_at=n WHERE owner_id=c.target_user_id AND state='active';
  UPDATE public.mfa_credentials SET state='active',confirmed_at=n WHERE id=f.id;
  UPDATE public.recovery_code_groups SET revoked_at=n WHERE owner_id=c.target_user_id AND revoked_at IS NULL;
  UPDATE public.users SET security_epoch=security_epoch+1,revision=revision+1,updated_at=n WHERE id=c.target_user_id;
  PERFORM identity_api.revoke_derived(c.target_user_id);
  UPDATE identity_control.manual_recovery_capabilities SET consumed_at=n WHERE case_id=c.id AND consumed_at IS NULL;
  UPDATE identity_control.identity_operation_proofs SET consumed_at=n WHERE id=proof.id;
  INSERT INTO identity_control.identity_control_audit(id,deployment_id,case_id,op_id,authority_id,actor_id,subject_id,action,target_id)
    VALUES((p->>'audit_id')::uuid,c.deployment_id,c.id,proof.op_id,c.executor_authority_id,c.executor_actor_user_id,c.target_user_id,'complete',c.id);
  UPDATE identity_control.manual_recovery_operations SET state='committed',committed_at=n,audit_id=(p->>'audit_id')::uuid WHERE id=proof.op_id;
  UPDATE identity_control.manual_recovery_cases SET state='completed',completed_op_id=proof.op_id WHERE id=c.id;
  INSERT INTO identity_control.identity_revocation_notices(id,subject_id,security_epoch) VALUES((p->>'notice_id')::uuid,c.target_user_id,c.target_epoch+1);
  RETURN jsonb_build_object('case_id',c.id,'op_id',proof.op_id,'stage','completed','result','committed','audit_id',p->>'audit_id','full_relogin_required',true,'recovery_codes_issued',false);
END $f$;

CREATE FUNCTION identity_api.receiver_peek(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE c identity_control.manual_recovery_cases%ROWTYPE;r identity_control.recovery_receivers%ROWTYPE;f public.mfa_credentials%ROWTYPE;cap identity_control.manual_recovery_capabilities%ROWTYPE;u record;context jsonb;n timestamptz;
BEGIN
  IF identity_api.control_guard(p) IS NULL THEN RETURN NULL;END IF;
  SELECT * INTO c FROM identity_control.manual_recovery_cases WHERE deployment_id=(p->>'deployment_id')::uuid AND id=(p->>'case_id')::uuid;
  IF c.id IS NULL THEN RETURN NULL;END IF;
  -- Include issuer FK/session parents before case/receiver/proof/factor locks.
  PERFORM id FROM public.users WHERE id IN(c.target_user_id,CASE WHEN c.executor_type='actor' THEN c.executor_id END) ORDER BY id FOR UPDATE;
  context:=identity_api.control_executor(p||jsonb_build_object('executor_type',c.executor_type,'executor_id',c.executor_id,
    'actor_session_hash',c.issuer_session_hash,'auth_context_deadline',c.pending_flow_expiry));
  IF context IS NULL THEN RETURN NULL;END IF;
  SELECT * INTO c FROM identity_control.manual_recovery_cases WHERE id=c.id FOR UPDATE;
  SELECT * INTO r FROM identity_control.recovery_receivers WHERE id=c.receiver_id AND id=(p->>'receiver_id')::uuid FOR UPDATE;
  SELECT * INTO cap FROM identity_control.manual_recovery_capabilities WHERE case_id=c.id AND receiver_id=r.id AND flow_generation=c.flow_generation AND consumed_at IS NULL FOR UPDATE;
  n:=clock_timestamp();
  IF r.id IS NULL OR cap.id IS NULL OR c.state NOT IN('pending','pending_confirmed') OR n>=least(c.pending_flow_expiry,r.expires_at,cap.expires_at,c.verification_deadline,(context->>'expires_at')::timestamptz)
    OR c.issued_version IS DISTINCT FROM (p->>'version')::bigint OR c.issued_manifest_hash IS DISTINCT FROM p->>'manifest_hash'
    OR c.flow_generation IS DISTINCT FROM (p->>'flow_generation')::bigint OR r.nonce_hash IS DISTINCT FROM p->>'nonce_hash' THEN RETURN NULL;END IF;
  SELECT id,role,state,deleted_at,password_hash,security_epoch INTO u FROM public.users WHERE id=c.target_user_id;
  IF u.state<>'active' OR u.deleted_at IS NOT NULL OR u.role<>c.target_role OR u.security_epoch<>c.target_epoch THEN RETURN NULL;END IF;
  IF p->>'action' NOT IN('await','factor_start','factor_confirm','abandon') OR (p->>'action'<>'await' AND cap.token_hash IS DISTINCT FROM p->>'cap_hash') THEN RETURN NULL;END IF;
  SELECT * INTO f FROM public.mfa_credentials WHERE owner_id=u.id AND id=c.pending_factor_id AND state='pending' FOR UPDATE;
  RETURN jsonb_build_object('owner_id',u.id,'target_role',u.role,'password_hash',u.password_hash,'public_key',r.public_key,'factor_id',f.id,'encrypted_seed',f.encrypted_seed,'now',clock_timestamp(),'pending_flow_expiry',c.pending_flow_expiry);
END $f$;

CREATE FUNCTION identity_api.confirm_manual_receiver(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE c identity_control.manual_recovery_cases%ROWTYPE;f public.mfa_credentials%ROWTYPE;facts jsonb;n timestamptz;next_version bigint;
BEGIN
  facts:=identity_api.receiver_peek(p);IF facts IS NULL THEN RETURN NULL;END IF;
  SELECT * INTO c FROM identity_control.manual_recovery_cases WHERE id=(p->>'case_id')::uuid;
  n:=clock_timestamp();
  IF p->>'action'='factor_start' THEN
    -- A lost seed response needs a fresh operator reissue; never rotate/replay
    -- a seed just because the receiver knows its next nonce.
    IF c.pending_factor_id IS NOT NULL OR c.state<>'pending' OR (c.target_role='student' AND facts->>'password_hash' IS DISTINCT FROM p->>'password_hash') THEN RETURN NULL;END IF;
    SELECT coalesce(max(version),0)+1 INTO next_version FROM public.mfa_credentials WHERE owner_id=c.target_user_id;
    INSERT INTO public.mfa_credentials(id,owner_id,state,version,encrypted_seed,expires_at,manual_deployment_id,manual_case_id)
      VALUES((p->>'factor_id')::uuid,c.target_user_id,'pending',next_version,p->>'encrypted_seed',c.pending_flow_expiry,c.deployment_id,c.id);
    UPDATE identity_control.manual_recovery_cases SET state='pending',pending_factor_id=(p->>'factor_id')::uuid,confirmation_hash=NULL WHERE id=c.id;
  ELSIF p->>'action'='factor_confirm' THEN
    SELECT * INTO f FROM public.mfa_credentials WHERE owner_id=c.target_user_id AND id=c.pending_factor_id AND manual_case_id=c.id AND state='pending' FOR UPDATE;n:=clock_timestamp();
    IF c.state<>'pending' OR f.id IS NULL OR f.id IS DISTINCT FROM (p->>'factor_id')::uuid OR f.encrypted_seed IS DISTINCT FROM p->>'encrypted_seed' OR n>=least(c.pending_flow_expiry,f.expires_at)
      OR (p->>'step')::bigint IS NULL OR (p->>'step')::bigint<=f.last_accepted_step OR abs((p->>'step')::bigint-floor(extract(epoch FROM n)/30)::bigint)>1 THEN RETURN NULL;END IF;
    UPDATE public.mfa_credentials SET last_accepted_step=(p->>'step')::bigint WHERE id=f.id;
    UPDATE identity_control.manual_recovery_cases SET state='pending_confirmed',confirmation_hash=p->>'confirmation_hash' WHERE id=c.id;
  ELSIF p->>'action'='abandon' THEN
    UPDATE public.mfa_credentials SET state='revoked',revoked_at=n WHERE owner_id=c.target_user_id AND manual_case_id=c.id AND state='pending';
    UPDATE identity_control.manual_recovery_capabilities SET consumed_at=n WHERE case_id=c.id AND consumed_at IS NULL;
    UPDATE identity_control.manual_recovery_cases SET state='abandoned' WHERE id=c.id;
  ELSIF p->>'action'<>'await' THEN RETURN NULL;END IF;
  UPDATE identity_control.recovery_receivers SET nonce_hash=p->>'next_nonce_hash' WHERE id=c.receiver_id;
  RETURN jsonb_build_object('case_id',c.id,'flow_generation',c.flow_generation,'pending_flow_expiry',c.pending_flow_expiry,'pending_factor_id',CASE WHEN p->>'action'='factor_start' THEN p->>'factor_id' ELSE c.pending_factor_id::text END,
    'stage',CASE WHEN p->>'action'='factor_confirm' THEN 'pending_confirmed' WHEN p->>'action'='abandon' THEN 'abandoned' ELSE 'pending' END,'confirmation_hash',p->>'confirmation_hash');
END $f$;

CREATE FUNCTION identity_api.read_manual_status(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE c record;o record;context jsonb;n timestamptz;result text:='unknown';stage text:=NULL;audit uuid:=NULL;
BEGIN
  IF identity_api.control_guard(p) IS NULL THEN RETURN NULL;END IF;
  context:=identity_api.control_executor(p);IF context IS NULL THEN RETURN NULL;END IF;
  SELECT id,deployment_id,executor_type,executor_id,state,flow_generation,pending_flow_expiry INTO c FROM identity_control.manual_recovery_cases
    WHERE deployment_id=(p->>'deployment_id')::uuid AND id=(p->>'case_id')::uuid FOR SHARE;
  IF c.id IS NOT NULL AND c.executor_type=p->>'executor_type' AND c.executor_id=(p->>'executor_id')::uuid THEN
    SELECT id,state,audit_id,committed_at INTO o FROM identity_control.manual_recovery_operations WHERE deployment_id=c.deployment_id AND case_id=c.id AND id=(p->>'op_id')::uuid FOR SHARE;
    IF o.id IS NOT NULL THEN
      IF o.state='reserved' THEN result:='not_committed';stage:=CASE WHEN c.state='open' THEN 'verified_case' ELSE c.state END;
      ELSIF o.state='committed' AND EXISTS(SELECT FROM identity_control.identity_control_audit WHERE id=o.audit_id AND deployment_id=c.deployment_id AND case_id=c.id AND op_id=o.id) THEN result:='committed';stage:=CASE WHEN c.state='open' THEN 'verified_case' ELSE c.state END;audit:=o.audit_id;END IF;
    END IF;
  END IF;
  n:=clock_timestamp();
  IF p->>'operation_deadline' IS NULL OR n>=(p->>'operation_deadline')::timestamptz OR (p->>'operation_deadline')::timestamptz>(p->>'verified_at')::timestamptz+interval '120 seconds' OR n<(p->>'verified_at')::timestamptz OR n>=(context->>'expires_at')::timestamptz THEN RETURN NULL;END IF;
  RETURN jsonb_build_object('protocol_version',1,'kind','recovery.status.response','case_id',p->>'case_id','op_id',p->>'op_id','result',result,'stage',stage,'audit_id',audit,'as_of',n);
END $f$;

CREATE FUNCTION identity_api.bind_control_broker(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE g identity_control.identity_deployment_guards%ROWTYPE;n integer;
BEGIN
  IF jsonb_typeof(p->'service_logins') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'service_logins')<>4
    OR NOT EXISTS(SELECT FROM jsonb_array_elements_text(p->'service_logins') x WHERE x=session_user)
    OR (SELECT count(DISTINCT value) FROM jsonb_array_elements_text(p->'service_logins'))<>4 THEN RETURN NULL;END IF;
  SELECT * INTO g FROM identity_control.identity_deployment_guards WHERE deployment_id=(p->>'deployment_id')::uuid FOR UPDATE;
  IF g.deployment_id IS NULL OR g.version IS DISTINCT FROM (p->>'version')::bigint OR g.manifest_hash IS DISTINCT FROM p->>'manifest_hash'
    OR g.state='applying' OR g.transition_id IS NOT NULL OR p->>'broker_instance_id' IS NULL THEN RETURN NULL;END IF;
  SELECT count(*) INTO n FROM pg_catalog.pg_stat_activity WHERE pid<>pg_backend_pid() AND usename IN(SELECT jsonb_array_elements_text(p->'service_logins'));
  IF n<>0 THEN RETURN NULL;END IF;
  UPDATE identity_control.identity_deployment_guards SET state='ready',broker_instance_id=(p->>'broker_instance_id')::uuid WHERE deployment_id=g.deployment_id;
  RETURN jsonb_build_object('broker_instance_id',p->>'broker_instance_id');
END $f$;

CREATE FUNCTION identity_api.issue_control_reauth(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE g identity_control.identity_deployment_guards%ROWTYPE;c identity_control.manual_recovery_cases%ROWTYPE;s jsonb;
  a public.auth_challenges%ROWTYPE;permission identity_control.recovery_permissions%ROWTYPE;uid uuid;n timestamptz;until_at timestamptz;
BEGIN
  IF p->>'purpose' NOT IN('student_recovery_issue','student_recovery_reissue','student_recovery_complete','student_recovery_status') THEN RETURN NULL;END IF;
  SELECT * INTO g FROM identity_control.identity_deployment_guards WHERE deployment_id=(p->>'deployment_id')::uuid FOR SHARE;
  IF g.deployment_id IS NULL OR g.state<>'ready' OR g.broker_instance_id IS NULL THEN RETURN NULL;END IF;
  SELECT owner_id INTO uid FROM public.auth_sessions WHERE token_hash=p->>'token_hash';IF uid IS NULL THEN RETURN NULL;END IF;
  SELECT * INTO c FROM identity_control.manual_recovery_cases WHERE deployment_id=g.deployment_id AND id=(p->>'case_id')::uuid;
  PERFORM id FROM public.users WHERE id IN(uid,CASE WHEN p->>'purpose'<>'student_recovery_status' THEN c.target_user_id END) ORDER BY id FOR SHARE;
  s:=identity_api.validate_session(jsonb_build_object('token_hash',p->>'token_hash','touch',false));
  IF s IS NULL OR s->>'role'<>'admin' OR s->>'mfa_level'<>'totp' THEN RETURN NULL;END IF;
  SELECT * INTO permission FROM identity_control.recovery_permissions WHERE deployment_id=g.deployment_id AND actor_id=uid FOR SHARE;
  SELECT * INTO a FROM public.auth_challenges WHERE token_hash=p->>'proof_hash' AND kind='verified' AND owner_id=uid FOR UPDATE;n:=clock_timestamp();
  IF permission.actor_id IS NULL OR NOT permission.enabled OR permission.version<>g.version OR n>=permission.expires_at OR a.id IS NULL OR a.consumed_at IS NOT NULL OR n>=a.expires_at
    OR a.scope->>'mode'<>'reauth' OR a.scope->>'mfa_level'<>'totp' OR a.scope->>'binding' IS DISTINCT FROM p->>'commitment' OR a.security_epoch<>(s->>'epoch')::bigint THEN RETURN NULL;END IF;
  IF c.id IS NOT NULL AND (c.executor_type<>'actor' OR c.executor_id<>uid) THEN RETURN NULL;END IF;
  IF p->>'purpose'<>'student_recovery_status' AND (c.id IS NULL OR c.target_role<>'student' OR NOT EXISTS(SELECT FROM public.users WHERE id=c.target_user_id AND role='student' AND security_epoch=c.target_epoch AND state='active' AND deleted_at IS NULL)) THEN RETURN NULL;END IF;
  until_at:=least((a.scope->>'verified_at')::timestamptz+interval '120 seconds',(s->>'expires_at')::timestamptz,permission.expires_at);
  IF n>=until_at THEN RETURN NULL;END IF;
  INSERT INTO public.reauth_proofs(id,owner_id,session_id,device_id,security_epoch,token_hash,purpose,target_id,commitment,operation_key,verified_at,expires_at,control_deployment_id,control_op_id,control_plan_hash)
    VALUES((p->>'id')::uuid,uid,(s->>'session_id')::uuid,(s->>'device_id')::uuid,a.security_epoch,p->>'reauth_hash',p->>'purpose',(p->>'case_id')::uuid,p->>'commitment',(p->>'op_id')::uuid,
      (a.scope->>'verified_at')::timestamptz,until_at,g.deployment_id,(p->>'op_id')::uuid,p->>'plan_hash');
  UPDATE public.auth_challenges SET consumed_at=n WHERE id=a.id;
  RETURN jsonb_build_object('expires_at',until_at);
END $f$;

CREATE FUNCTION identity_api.verify_control_bridge(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE s jsonb;r public.reauth_proofs%ROWTYPE;n timestamptz;
BEGIN
  s:=identity_api.validate_session(jsonb_build_object('token_hash',p->>'token_hash','touch',false));
  IF s IS NULL OR s->>'role'<>'admin' OR s->>'mfa_level'<>'totp' THEN RETURN NULL;END IF;
  SELECT * INTO r FROM public.reauth_proofs WHERE token_hash=p->>'reauth_hash' AND owner_id=(s->>'owner_id')::uuid FOR SHARE;n:=clock_timestamp();
  IF r.id IS NULL OR r.consumed_at IS NOT NULL OR n>=r.expires_at OR r.security_epoch<>(s->>'epoch')::bigint OR r.session_id<>(s->>'session_id')::uuid OR r.device_id<>(s->>'device_id')::uuid
    OR r.purpose IS DISTINCT FROM 'student_recovery_'||(p->>'action') OR r.control_deployment_id IS DISTINCT FROM (p->>'deployment_id')::uuid
    OR r.target_id IS DISTINCT FROM (p->>'case_id')::uuid OR r.control_op_id IS DISTINCT FROM (p->>'op_id')::uuid OR r.control_plan_hash IS DISTINCT FROM p->>'plan_hash' THEN RETURN NULL;END IF;
  RETURN jsonb_build_object('verified_at',r.verified_at,'operation_deadline',r.expires_at,'executor_id',r.owner_id);
END $f$;

CREATE FUNCTION identity_api.apply_registration_change(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE g identity_control.identity_deployment_guards%ROWTYPE;t identity_control.registration_transitions%ROWTYPE;n timestamptz;
BEGIN
  SELECT * INTO g FROM identity_control.identity_deployment_guards WHERE deployment_id=(p->>'deployment_id')::uuid FOR UPDATE;
  IF g.deployment_id IS NULL OR g.broker_instance_id IS DISTINCT FROM (p->>'broker_instance_id')::uuid THEN RETURN NULL;END IF;
  SELECT * INTO t FROM identity_control.registration_transitions WHERE id=(p->>'transition_id')::uuid;
  IF t.id IS NOT NULL THEN
    IF t.deployment_id=g.deployment_id AND t.new_version=(p->>'new_version')::bigint AND t.new_manifest_hash=p->>'new_manifest_hash'
      AND t.old_version=(p->>'old_version')::bigint AND t.old_manifest_hash=p->>'old_manifest_hash'
      AND t.approval_hash=p->>'approval_hash' AND t.nonce_hash=p->>'nonce_hash' THEN
      RETURN jsonb_build_object('phase','pg_committed','audit_id',t.audit_id);
    END IF;
    RETURN NULL;
  END IF;
  n:=clock_timestamp();
  IF g.state<>'ready' OR g.transition_id IS NOT NULL OR g.version IS DISTINCT FROM (p->>'old_version')::bigint OR g.manifest_hash IS DISTINCT FROM p->>'old_manifest_hash'
    OR (p->>'new_version')::bigint<=g.version OR p->>'approval_deadline' IS NULL OR n>=(p->>'approval_deadline')::timestamptz THEN RETURN NULL;END IF;
  PERFORM id FROM identity_control.recovery_authorities WHERE deployment_id=g.deployment_id AND id=(p->>'authority_id')::uuid FOR UPDATE;
  IF NOT FOUND THEN RETURN NULL;END IF;
  -- No Web user/counter/Outbox is involved in authority maintenance.
  UPDATE identity_control.recovery_authorities SET version=(p->>'new_version')::bigint,manifest_hash=p->>'new_manifest_hash',
    os_mapping_hash=p->>'os_mapping_hash',enabled=(p->>'enabled')::boolean WHERE deployment_id=g.deployment_id AND id=(p->>'authority_id')::uuid;
  UPDATE identity_control.identity_deployment_guards SET version=(p->>'new_version')::bigint,manifest_hash=p->>'new_manifest_hash',state='applying',transition_id=(p->>'transition_id')::uuid WHERE deployment_id=g.deployment_id;
  INSERT INTO identity_control.identity_control_audit(id,deployment_id,authority_id,action,target_id)
    VALUES((p->>'audit_id')::uuid,g.deployment_id,(p->>'authority_id')::uuid,'registry_change',(p->>'transition_id')::uuid);
  INSERT INTO identity_control.registration_transitions(id,deployment_id,old_version,new_version,old_manifest_hash,new_manifest_hash,approval_hash,nonce_hash,committed_at,audit_id)
    VALUES((p->>'transition_id')::uuid,g.deployment_id,g.version,(p->>'new_version')::bigint,g.manifest_hash,p->>'new_manifest_hash',p->>'approval_hash',p->>'nonce_hash',n,(p->>'audit_id')::uuid);
  INSERT INTO identity_control.registry_revocation_notices(id,deployment_id,version) VALUES((p->>'transition_id')::uuid,g.deployment_id,(p->>'new_version')::bigint);
  RETURN jsonb_build_object('phase','pg_committed','audit_id',p->>'audit_id');
END $f$;

CREATE FUNCTION identity_api.publish_registration_ready(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE g identity_control.identity_deployment_guards%ROWTYPE;
BEGIN
  SELECT * INTO g FROM identity_control.identity_deployment_guards WHERE deployment_id=(p->>'deployment_id')::uuid FOR UPDATE;
  IF g.deployment_id IS NULL OR g.broker_instance_id IS DISTINCT FROM (p->>'broker_instance_id')::uuid OR g.version IS DISTINCT FROM (p->>'version')::bigint OR g.manifest_hash IS DISTINCT FROM p->>'manifest_hash' THEN RETURN NULL;END IF;
  IF g.state='ready' AND g.transition_id IS NULL AND EXISTS(SELECT FROM identity_control.registration_transitions WHERE id=(p->>'transition_id')::uuid AND deployment_id=g.deployment_id AND new_version=g.version AND new_manifest_hash=g.manifest_hash) THEN RETURN jsonb_build_object('phase','published_ready');END IF;
  IF g.state<>'applying' OR g.transition_id IS DISTINCT FROM (p->>'transition_id')::uuid THEN RETURN NULL;END IF;
  UPDATE identity_control.identity_deployment_guards SET state='ready',transition_id=NULL WHERE deployment_id=g.deployment_id;
  RETURN jsonb_build_object('phase','published_ready');
END $f$;

CREATE FUNCTION public.classroom_web_touch_session(uid uuid,sid uuid,did uuid,epoch bigint,expected_role text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $f$
DECLARE s public.auth_sessions%ROWTYPE;d record;n timestamptz;
BEGIN
  IF uid IS DISTINCT FROM nullif(current_setting('app.owner_id',true),'')::uuid THEN RETURN false;END IF;
  SELECT id,revoked_at INTO d FROM public.devices WHERE owner_id=uid AND id=did FOR SHARE;
  -- Successful user activity takes UPDATE at first acquisition. Two readers
  -- must never both take SHARE and subsequently attempt a touch upgrade.
  SELECT * INTO s FROM public.auth_sessions WHERE owner_id=uid AND id=sid AND device_id=did FOR UPDATE;n:=clock_timestamp();
  IF d.id IS NULL OR d.revoked_at IS NOT NULL OR s.id IS NULL OR s.revoked_at IS NOT NULL OR s.security_epoch<>epoch OR s.role<>expected_role
    OR n>=least(s.idle_deadline,s.absolute_deadline) OR (s.mfa_level='totp' AND NOT EXISTS(SELECT FROM public.mfa_credentials WHERE owner_id=uid AND id=s.factor_id AND state='active')) THEN RETURN false;END IF;
  UPDATE public.auth_sessions SET idle_deadline=least(absolute_deadline,n+CASE WHEN role='admin' THEN interval '15 minutes' ELSE interval '14 days' END),updated_at=n WHERE id=sid AND owner_id=uid;
  RETURN FOUND;
END $f$;

ALTER TABLE identity_control.identity_revocation_notices ADD COLUMN claim_hash text CHECK(claim_hash IS NULL OR claim_hash~'^[0-9a-f]{64}$');
ALTER TABLE identity_control.identity_revocation_notices ADD COLUMN claim_until timestamptz;
ALTER TABLE identity_control.registry_revocation_notices ADD COLUMN claim_hash text CHECK(claim_hash IS NULL OR claim_hash~'^[0-9a-f]{64}$');
ALTER TABLE identity_control.registry_revocation_notices ADD COLUMN claim_until timestamptz;
GRANT USAGE ON SCHEMA identity_control,identity_api TO classroom_web_identity_notice_writer;
GRANT SELECT,UPDATE(claim_hash,claim_until,delivered_at) ON identity_control.identity_revocation_notices,identity_control.registry_revocation_notices TO classroom_web_identity_notice_writer;
CREATE POLICY notice_claim ON identity_control.identity_revocation_notices TO classroom_web_identity_notice_writer USING(true) WITH CHECK(true);
CREATE POLICY registry_notice_claim ON identity_control.registry_revocation_notices TO classroom_web_identity_notice_writer USING(true) WITH CHECK(true);

CREATE FUNCTION identity_api.claim_identity_notices(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,pg_temp AS $f$
DECLARE row record;items jsonb:='[]';n timestamptz:=clock_timestamp();count_limit integer:=(p->>'limit')::integer;
BEGIN
  IF p->>'claim_hash' IS NULL OR p->>'claim_hash' !~ '^[0-9a-f]{64}$' OR count_limit NOT BETWEEN 1 AND 32 THEN RETURN NULL;END IF;
  -- Only event rows are locked here. No user/case/business counter/Outbox is
  -- read while holding an event; callers receive this batch after COMMIT.
  FOR row IN SELECT id,subject_id,security_epoch FROM identity_control.identity_revocation_notices
    WHERE delivered_at IS NULL AND (claim_until IS NULL OR claim_until<=n) ORDER BY created_at,id LIMIT count_limit FOR UPDATE SKIP LOCKED LOOP
    UPDATE identity_control.identity_revocation_notices SET claim_hash=p->>'claim_hash',claim_until=n+interval '30 seconds' WHERE id=row.id;
    items:=items||jsonb_build_array(jsonb_build_object('id',row.id,'scope','user','subject_id',row.subject_id,'security_epoch',row.security_epoch));
  END LOOP;
  FOR row IN SELECT id,deployment_id,version FROM identity_control.registry_revocation_notices
    WHERE delivered_at IS NULL AND (claim_until IS NULL OR claim_until<=n) ORDER BY created_at,id LIMIT count_limit-jsonb_array_length(items) FOR UPDATE SKIP LOCKED LOOP
    UPDATE identity_control.registry_revocation_notices SET claim_hash=p->>'claim_hash',claim_until=n+interval '30 seconds' WHERE id=row.id;
    items:=items||jsonb_build_array(jsonb_build_object('id',row.id,'scope','registry','deployment_id',row.deployment_id,'version',row.version));
  END LOOP;
  RETURN jsonb_build_object('items',items);
END $f$;

CREATE FUNCTION identity_api.finish_identity_notice(p jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,pg_temp AS $f$
BEGIN
  IF p->>'scope'='user' THEN
    UPDATE identity_control.identity_revocation_notices SET delivered_at=clock_timestamp(),claim_until=NULL,claim_hash=NULL
      WHERE id=(p->>'id')::uuid AND delivered_at IS NULL AND claim_hash=p->>'claim_hash' AND clock_timestamp()<claim_until;
  ELSIF p->>'scope'='registry' THEN
    UPDATE identity_control.registry_revocation_notices SET delivered_at=clock_timestamp(),claim_until=NULL,claim_hash=NULL
      WHERE id=(p->>'id')::uuid AND delivered_at IS NULL AND claim_hash=p->>'claim_hash' AND clock_timestamp()<claim_until;
  ELSE RETURN false;END IF;
  RETURN FOUND;
END $f$;
CREATE FUNCTION identity_api.registration_change_status(p jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,pg_temp AS $f$
BEGIN
  PERFORM deployment_id FROM identity_control.identity_deployment_guards WHERE deployment_id=(p->>'deployment_id')::uuid FOR SHARE;
  IF NOT FOUND THEN RETURN false;END IF;
  RETURN EXISTS(SELECT FROM identity_control.registration_transitions WHERE id=(p->>'transition_id')::uuid AND deployment_id=(p->>'deployment_id')::uuid
    AND new_version=(p->>'new_version')::bigint AND new_manifest_hash=p->>'new_manifest_hash' AND old_version=(p->>'old_version')::bigint AND old_manifest_hash=p->>'old_manifest_hash'
    AND approval_hash=p->>'approval_hash' AND nonce_hash=p->>'nonce_hash');
END $f$;

CREATE FUNCTION identity_api.adopt_registration_transition(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,pg_temp AS $f$
DECLARE g identity_control.identity_deployment_guards%ROWTYPE;n timestamptz;
BEGIN
  IF jsonb_typeof(p->'service_logins') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'service_logins')<>4
    OR NOT EXISTS(SELECT FROM jsonb_array_elements_text(p->'service_logins') x WHERE x=session_user)
    OR (SELECT count(DISTINCT value) FROM jsonb_array_elements_text(p->'service_logins'))<>4 THEN RETURN NULL;END IF;
  SELECT * INTO g FROM identity_control.identity_deployment_guards WHERE deployment_id=(p->>'deployment_id')::uuid FOR UPDATE;n:=clock_timestamp();
  IF g.deployment_id IS NULL OR p->>'broker_instance_id' IS NULL OR p->>'external_current_deadline' IS NULL OR n>=(p->>'external_current_deadline')::timestamptz
    OR EXISTS(SELECT FROM pg_stat_activity WHERE pid<>pg_backend_pid() AND usename IN(SELECT jsonb_array_elements_text(p->'service_logins'))) THEN RETURN NULL;END IF;
  IF g.state='ready' AND g.transition_id IS NULL AND g.version=(p->>'old_version')::bigint AND g.manifest_hash=p->>'old_manifest_hash' THEN
    IF p->>'approval_deadline' IS NULL OR n>=(p->>'approval_deadline')::timestamptz THEN RETURN NULL;END IF;
  ELSIF g.state='applying' AND g.transition_id=(p->>'transition_id')::uuid AND g.version=(p->>'new_version')::bigint AND g.manifest_hash=p->>'new_manifest_hash' THEN
    IF NOT identity_api.registration_change_status(p) THEN RETURN NULL;END IF;
  ELSE RETURN NULL;END IF;
  UPDATE identity_control.identity_deployment_guards SET broker_instance_id=(p->>'broker_instance_id')::uuid WHERE deployment_id=g.deployment_id;
  RETURN jsonb_build_object('broker_instance_id',p->>'broker_instance_id');
END $f$;

CREATE FUNCTION identity_api.queue_auth_mail(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE u record;n timestamptz;
BEGIN
  PERFORM pg_advisory_xact_lock(7303002);n:=clock_timestamp();
  IF p->>'encrypted_mail' IS NULL OR (SELECT count(*) FROM identity_control.authentication_mail_jobs WHERE state IN('queued','sending') AND expires_at>n)>=200 THEN RETURN jsonb_build_object('error','RATE_LIMITED');END IF;
  SELECT id,security_epoch INTO u FROM public.users WHERE normalized_email=p->>'email' AND state='active' AND deleted_at IS NULL AND email_verified_at IS NOT NULL FOR SHARE;
  IF u.id IS NOT NULL THEN
    INSERT INTO public.email_actions(id,owner_id,purpose,token_hash,security_epoch,expires_at)
      VALUES((p->>'id')::uuid,u.id,'reset',p->>'action_hash',u.security_epoch,n+interval '30 minutes');
  END IF;
  -- Both existence arms commit the same opaque spool shape. SMTP failures and
  -- recipient-dependent work cannot change the public acknowledgement.
  INSERT INTO identity_control.authentication_mail_jobs(id,owner_id,action_id,encrypted_payload,expires_at)
    VALUES((p->>'id')::uuid,u.id,CASE WHEN u.id IS NOT NULL THEN (p->>'id')::uuid END,p->>'encrypted_mail',n+interval '30 minutes');
  RETURN '{}'::jsonb;
END $f$;

CREATE FUNCTION identity_api.claim_auth_mail(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,pg_temp AS $f$
DECLARE item record;n timestamptz:=clock_timestamp();items jsonb:='[]';lim integer;
BEGIN
  IF p->>'claim_hash' IS NULL OR p->>'claim_hash' !~ '^[0-9a-f]{64}$' THEN RETURN NULL;END IF;
  lim:=least(greatest(coalesce((p->>'limit')::integer,32),1),32);
  -- Retire expired or exhausted jobs first so they never hide deliverable ones behind a small limit.
  UPDATE identity_control.authentication_mail_jobs SET state='discarded',encrypted_payload=NULL,finished_at=n,claim_until=NULL,claim_hash=NULL
    WHERE id IN (SELECT id FROM identity_control.authentication_mail_jobs WHERE state IN('queued','sending') AND (claim_until IS NULL OR claim_until<=n)
      AND (expires_at<=n OR attempts>=3) ORDER BY created_at,id LIMIT 32 FOR UPDATE SKIP LOCKED);
  -- A claim spends a delivery attempt and starts a 30 second lease. The dispatcher sends
  -- sequentially, so it claims one job at a time (limit=1) immediately before sending.
  FOR item IN SELECT id FROM identity_control.authentication_mail_jobs WHERE state IN('queued','sending') AND (claim_until IS NULL OR claim_until<=n)
      AND expires_at>n AND attempts<3 ORDER BY created_at,id LIMIT lim FOR UPDATE SKIP LOCKED LOOP
    UPDATE identity_control.authentication_mail_jobs SET state='sending',attempts=attempts+1,claim_hash=p->>'claim_hash',claim_until=n+interval '30 seconds' WHERE id=item.id;
    items:=items||jsonb_build_array(item.id);
  END LOOP;
  RETURN items;
END $f$;

CREATE FUNCTION identity_api.read_auth_mail_delivery(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE j identity_control.authentication_mail_jobs%ROWTYPE;a record;u record;n timestamptz;
BEGIN
  SELECT * INTO j FROM identity_control.authentication_mail_jobs WHERE id=(p->>'id')::uuid;
  IF j.id IS NULL OR j.owner_id IS NULL THEN RETURN NULL;END IF;
  SELECT id,state,deleted_at,email_verified_at,security_epoch INTO u FROM public.users WHERE id=j.owner_id FOR SHARE;
  SELECT id,owner_id,purpose,security_epoch,expires_at,consumed_at INTO a FROM public.email_actions WHERE owner_id=j.owner_id AND id=j.action_id FOR SHARE;
  SELECT * INTO j FROM identity_control.authentication_mail_jobs WHERE id=j.id FOR SHARE;n:=clock_timestamp();
  IF j.state<>'sending' OR j.claim_hash IS DISTINCT FROM p->>'claim_hash' OR n>=least(j.expires_at,j.claim_until,a.expires_at) OR a.id IS NULL OR a.consumed_at IS NOT NULL
    OR u.state<>'active' OR u.deleted_at IS NOT NULL OR u.email_verified_at IS NULL OR u.security_epoch<>a.security_epoch THEN RETURN NULL;END IF;
  RETURN jsonb_build_object('encrypted_payload',j.encrypted_payload);
END $f$;

CREATE FUNCTION identity_api.finish_auth_mail(p jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,pg_temp AS $f$
BEGIN
  IF p->>'result' NOT IN('sent','discarded') THEN RETURN false;END IF;
  UPDATE identity_control.authentication_mail_jobs SET state=p->>'result',encrypted_payload=NULL,finished_at=clock_timestamp(),claim_until=NULL,claim_hash=NULL
    WHERE id=(p->>'id')::uuid AND state='sending' AND claim_hash=p->>'claim_hash' AND clock_timestamp()<claim_until;
  RETURN FOUND;
END $f$;

-- FINAL CONTROL PRIVILEGES: keep this section after all control definitions.
GRANT CREATE ON SCHEMA identity_api TO classroom_web_session_writer;
ALTER FUNCTION identity_api.queue_auth_mail(jsonb) OWNER TO classroom_web_session_writer;
REVOKE CREATE ON SCHEMA identity_api FROM classroom_web_session_writer;
GRANT CREATE ON SCHEMA identity_api TO classroom_web_auth_mail_writer;
ALTER FUNCTION identity_api.claim_auth_mail(jsonb) OWNER TO classroom_web_auth_mail_writer;
ALTER FUNCTION identity_api.read_auth_mail_delivery(jsonb) OWNER TO classroom_web_auth_mail_writer;
ALTER FUNCTION identity_api.finish_auth_mail(jsonb) OWNER TO classroom_web_auth_mail_writer;
REVOKE CREATE ON SCHEMA identity_api FROM classroom_web_auth_mail_writer;
GRANT CREATE ON SCHEMA identity_api TO classroom_web_identity_notice_writer;
ALTER FUNCTION identity_api.claim_identity_notices(jsonb) OWNER TO classroom_web_identity_notice_writer;
ALTER FUNCTION identity_api.finish_identity_notice(jsonb) OWNER TO classroom_web_identity_notice_writer;
REVOKE CREATE ON SCHEMA identity_api FROM classroom_web_identity_notice_writer;
GRANT CREATE ON SCHEMA public TO classroom_web_session_writer;
ALTER FUNCTION public.classroom_web_touch_session(uuid,uuid,uuid,bigint,text) OWNER TO classroom_web_session_writer;
REVOKE CREATE ON SCHEMA public FROM classroom_web_session_writer;
REVOKE ALL ON FUNCTION public.classroom_web_touch_session(uuid,uuid,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.classroom_web_touch_session(uuid,uuid,uuid,bigint,text) TO classroom_web_runtime;
GRANT CREATE ON SCHEMA identity_api TO classroom_web_manual_recovery_writer;
DO $functions$
DECLARE r record;
BEGIN
  FOR r IN SELECT p.oid::regprocedure AS signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='identity_api' AND p.proname IN('control_guard','control_executor','open_manual_case','manual_case_plan','register_receiver','prepare_manual_proof','issue_manual','complete_manual','receiver_peek','confirm_manual_receiver','issue_control_reauth') LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO classroom_web_manual_recovery_writer',r.signature);
  END LOOP;
END $functions$;
REVOKE CREATE ON SCHEMA identity_api FROM classroom_web_manual_recovery_writer;
GRANT CREATE ON SCHEMA identity_api TO classroom_web_manual_status_reader;
ALTER FUNCTION identity_api.read_manual_status(jsonb) OWNER TO classroom_web_manual_status_reader;
REVOKE CREATE ON SCHEMA identity_api FROM classroom_web_manual_status_reader;
GRANT CREATE ON SCHEMA identity_api TO classroom_web_registration_maintainer;
ALTER FUNCTION identity_api.bind_control_broker(jsonb) OWNER TO classroom_web_registration_maintainer;
ALTER FUNCTION identity_api.apply_registration_change(jsonb) OWNER TO classroom_web_registration_maintainer;
ALTER FUNCTION identity_api.publish_registration_ready(jsonb) OWNER TO classroom_web_registration_maintainer;
ALTER FUNCTION identity_api.registration_change_status(jsonb) OWNER TO classroom_web_registration_maintainer;
ALTER FUNCTION identity_api.adopt_registration_transition(jsonb) OWNER TO classroom_web_registration_maintainer;
REVOKE CREATE ON SCHEMA identity_api FROM classroom_web_registration_maintainer;
GRANT CREATE ON SCHEMA identity_api TO classroom_web_session_writer;
ALTER FUNCTION identity_api.verify_control_bridge(jsonb) OWNER TO classroom_web_session_writer;
REVOKE CREATE ON SCHEMA identity_api FROM classroom_web_session_writer;
GRANT EXECUTE ON FUNCTION identity_api.control_guard(jsonb),identity_api.control_executor(jsonb) TO classroom_web_manual_status_reader;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA identity_api FROM PUBLIC;
