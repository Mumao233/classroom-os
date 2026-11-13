-- PR-03b. Fixed functions only; callers never obtain table ownership or DML.
CREATE SCHEMA IF NOT EXISTS identity_control;
CREATE SCHEMA identity_api;
REVOKE ALL ON SCHEMA identity_control,identity_api FROM PUBLIC;

DO $roles$
DECLARE n text;
BEGIN
  FOREACH n IN ARRAY ARRAY['classroom_web_identity_table_owner','classroom_web_auth_verifier','classroom_web_session_writer'] LOOP
    IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname=n) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION',n);
    ELSIF EXISTS(SELECT FROM pg_roles WHERE rolname=n AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication OR rolinherit)) THEN
      RAISE EXCEPTION 'unsafe preexisting authentication owner';
    END IF;
    IF EXISTS(SELECT FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=n)) THEN
      RAISE EXCEPTION 'authentication owners cannot inherit other roles';
    END IF;
  END LOOP;
END $roles$;

CREATE TABLE public.devices(
  id uuid PRIMARY KEY,owner_id uuid NOT NULL REFERENCES public.users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  revision bigint NOT NULL DEFAULT 1 CHECK(revision>=1),device_name text NOT NULL CHECK(char_length(device_name)<=100),
  last_seen timestamptz NOT NULL DEFAULT clock_timestamp(),revoked_at timestamptz,
  trusted boolean NOT NULL DEFAULT false CHECK(NOT trusted),key_epoch bigint NOT NULL DEFAULT 1 CHECK(key_epoch>=1),
  UNIQUE(owner_id,id)
);
CREATE TABLE public.auth_sessions(
  id uuid PRIMARY KEY,owner_id uuid NOT NULL REFERENCES public.users(id),device_id uuid NOT NULL,
  token_hash text UNIQUE NOT NULL CHECK(token_hash~'^[0-9a-f]{64}$'),
  logical_session_id uuid NOT NULL,established_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),revision bigint NOT NULL DEFAULT 1,
  security_epoch bigint NOT NULL CHECK(security_epoch>=1),role text NOT NULL CHECK(role IN('student','admin')),
  mfa_level text NOT NULL CHECK(mfa_level IN('none','totp')),factor_id uuid,
  idle_deadline timestamptz NOT NULL,absolute_deadline timestamptz NOT NULL,revoked_at timestamptz,
  FOREIGN KEY(owner_id,device_id) REFERENCES public.devices(owner_id,id),UNIQUE(owner_id,id),
  CHECK(idle_deadline<=absolute_deadline),CHECK(role<>'admin' OR mfa_level='totp')
);
CREATE INDEX auth_sessions_owner ON public.auth_sessions(owner_id,device_id);
CREATE TABLE public.mfa_credentials(
  id uuid PRIMARY KEY,owner_id uuid NOT NULL REFERENCES public.users(id),state text NOT NULL CHECK(state IN('pending','active','revoked')),
  version bigint NOT NULL DEFAULT 1 CHECK(version>=1),encrypted_seed text NOT NULL CHECK(char_length(encrypted_seed)<=1024),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),expires_at timestamptz,confirmed_at timestamptz,revoked_at timestamptz,
  last_accepted_step bigint NOT NULL DEFAULT -1,UNIQUE(owner_id,id),
  CHECK(state<>'pending' OR expires_at IS NOT NULL)
);
CREATE UNIQUE INDEX mfa_one_active ON public.mfa_credentials(owner_id) WHERE state='active';
ALTER TABLE public.auth_sessions ADD CONSTRAINT auth_session_factor FOREIGN KEY(owner_id,factor_id) REFERENCES public.mfa_credentials(owner_id,id);
CREATE TABLE public.recovery_code_groups(
  id uuid PRIMARY KEY,owner_id uuid NOT NULL REFERENCES public.users(id),credential_id uuid NOT NULL,
  generation bigint NOT NULL CHECK(generation>=1),security_epoch bigint NOT NULL,shown_at timestamptz NOT NULL DEFAULT clock_timestamp(),revoked_at timestamptz,
  FOREIGN KEY(owner_id,credential_id) REFERENCES public.mfa_credentials(owner_id,id),UNIQUE(owner_id,id),UNIQUE(owner_id,generation)
);
CREATE UNIQUE INDEX recovery_one_group ON public.recovery_code_groups(owner_id) WHERE revoked_at IS NULL;
CREATE TABLE public.mfa_recovery_codes(
  id uuid PRIMARY KEY,owner_id uuid NOT NULL,group_id uuid NOT NULL,code_hash text UNIQUE NOT NULL CHECK(code_hash~'^[0-9a-f]{64}$'),consumed_at timestamptz,
  FOREIGN KEY(owner_id,group_id) REFERENCES public.recovery_code_groups(owner_id,id)
);
CREATE TABLE public.auth_challenges(
  id uuid PRIMARY KEY,kind text NOT NULL CHECK(kind IN('preauth','login_mfa','verified','auto_recovery','ws')),
  owner_id uuid REFERENCES public.users(id),token_hash text UNIQUE NOT NULL CHECK(token_hash~'^[0-9a-f]{64}$'),
  parent_hash text,security_epoch bigint,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),expires_at timestamptz NOT NULL,
  consumed_at timestamptz,scope jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(scope)='object' AND pg_column_size(scope)<=16384),
  CHECK((kind='preauth')=(owner_id IS NULL))
);
CREATE INDEX auth_challenges_owner ON public.auth_challenges(owner_id,kind);
CREATE TABLE public.reauth_proofs(
  id uuid PRIMARY KEY,owner_id uuid NOT NULL,session_id uuid NOT NULL,device_id uuid NOT NULL,
  security_epoch bigint NOT NULL,token_hash text UNIQUE NOT NULL,purpose text NOT NULL,target_id uuid NOT NULL,
  commitment text NOT NULL CHECK(commitment~'^[0-9a-f]{64}$'),operation_key uuid NOT NULL,
  verified_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,consumed_at timestamptz,
  FOREIGN KEY(owner_id,session_id) REFERENCES public.auth_sessions(owner_id,id),FOREIGN KEY(owner_id,device_id) REFERENCES public.devices(owner_id,id),
  CHECK(expires_at>verified_at AND expires_at<=verified_at+interval '600 seconds')
);
CREATE TABLE public.email_actions(
  id uuid PRIMARY KEY,owner_id uuid NOT NULL REFERENCES public.users(id),purpose text NOT NULL CHECK(purpose IN('reset','change_email')),
  token_hash text UNIQUE NOT NULL CHECK(token_hash~'^[0-9a-f]{64}$'),security_epoch bigint NOT NULL,new_email text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),expires_at timestamptz NOT NULL,consumed_at timestamptz
);
CREATE TABLE public.preauth_receipts(
  context_hash text NOT NULL,operation text NOT NULL,operation_key uuid NOT NULL,commitment text NOT NULL,
  secret boolean NOT NULL DEFAULT false,result jsonb,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(context_hash,operation,operation_key),CHECK(NOT secret OR result IS NULL)
);
CREATE TABLE identity_control.identity_control_audit(
  id uuid PRIMARY KEY,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),actor_id uuid REFERENCES public.users(id),
  subject_id uuid REFERENCES public.users(id),action text NOT NULL CHECK(char_length(action)<=64),target_id uuid
);
CREATE TABLE identity_control.identity_revocation_notices(
  id uuid PRIMARY KEY,subject_id uuid NOT NULL REFERENCES public.users(id),security_epoch bigint NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),delivered_at timestamptz
);
CREATE TABLE identity_control.authentication_budgets(
  budget_key text PRIMARY KEY CHECK(budget_key~'^[0-9a-f]{64}$'),window_start timestamptz NOT NULL,n integer NOT NULL CHECK(n>=0)
);

-- No login is granted membership in the definer/table owners. Provisioning
-- grants only the exact function EXECUTE lists to separate NOINHERIT logins.
GRANT USAGE ON SCHEMA public,identity_control,identity_api TO classroom_web_auth_verifier,classroom_web_session_writer;
GRANT SELECT(id,normalized_email,password_hash,role,state,security_epoch,email_verified_at,deleted_at,profile,revision,created_at,updated_at)
  ON public.users TO classroom_web_auth_verifier,classroom_web_session_writer;
GRANT UPDATE(password_hash,normalized_email,security_epoch,revision,updated_at) ON public.users TO classroom_web_session_writer;
CREATE POLICY auth_verify_user ON public.users TO classroom_web_auth_verifier USING(true);
CREATE POLICY auth_session_user ON public.users TO classroom_web_session_writer USING(true) WITH CHECK(true);

DO $tables$
DECLARE n text;ns text;who text;
BEGIN
  FOREACH n IN ARRAY ARRAY['devices','auth_sessions','mfa_credentials','recovery_code_groups','mfa_recovery_codes','auth_challenges','reauth_proofs','email_actions','preauth_receipts'] LOOP
    EXECUTE format('ALTER TABLE public.%I OWNER TO classroom_web_identity_table_owner',n);
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',n);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',n);
    EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,classroom_web_runtime,classroom_web_identity,classroom_web_scheduler,classroom_web_task_dispatcher',n);
    EXECUTE format('GRANT SELECT,INSERT,UPDATE ON public.%I TO classroom_web_session_writer',n);
    EXECUTE format('CREATE POLICY session_control ON public.%I TO classroom_web_session_writer USING(true) WITH CHECK(true)',n);
  END LOOP;
  FOREACH n IN ARRAY ARRAY['identity_control_audit','identity_revocation_notices','authentication_budgets'] LOOP
    EXECUTE format('ALTER TABLE identity_control.%I OWNER TO classroom_web_identity_table_owner',n);
    EXECUTE format('ALTER TABLE identity_control.%I ENABLE ROW LEVEL SECURITY',n);
    EXECUTE format('ALTER TABLE identity_control.%I FORCE ROW LEVEL SECURITY',n);
    EXECUTE format('REVOKE ALL ON identity_control.%I FROM PUBLIC',n);
    EXECUTE format('GRANT SELECT,INSERT ON identity_control.%I TO classroom_web_session_writer',n);
    EXECUTE format('CREATE POLICY session_control ON identity_control.%I TO classroom_web_session_writer USING(true) WITH CHECK(true)',n);
  END LOOP;
END $tables$;
GRANT UPDATE ON identity_control.authentication_budgets TO classroom_web_session_writer;
GRANT SELECT ON public.auth_sessions,public.devices,public.mfa_credentials,public.auth_challenges,public.email_actions TO classroom_web_auth_verifier;
GRANT INSERT ON public.auth_challenges TO classroom_web_auth_verifier;
GRANT UPDATE(last_accepted_step) ON public.mfa_credentials TO classroom_web_auth_verifier;
GRANT UPDATE(id) ON public.users TO classroom_web_auth_verifier,classroom_web_session_writer;
CREATE POLICY auth_verification_sessions ON public.auth_sessions TO classroom_web_auth_verifier USING(true);
CREATE POLICY auth_verification_devices ON public.devices TO classroom_web_auth_verifier USING(true);
CREATE POLICY auth_verification_factors ON public.mfa_credentials TO classroom_web_auth_verifier USING(true) WITH CHECK(true);
CREATE POLICY auth_verification_challenges ON public.auth_challenges TO classroom_web_auth_verifier USING(true) WITH CHECK(kind='verified');
CREATE POLICY auth_verification_actions ON public.email_actions TO classroom_web_auth_verifier USING(true);

CREATE FUNCTION identity_api.locate_auth(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE u record;f public.mfa_credentials%ROWTYPE;c public.auth_challenges%ROWTYPE;now_at timestamptz:=clock_timestamp();
BEGIN
  IF p->>'lookup'='email' THEN SELECT id,role,state,deleted_at,email_verified_at,password_hash,security_epoch INTO u FROM public.users WHERE normalized_email=p->>'email';
  ELSIF p->>'lookup'='session' THEN
    SELECT x.id,x.role,x.state,x.deleted_at,x.email_verified_at,x.password_hash,x.security_epoch INTO u FROM public.users x JOIN public.auth_sessions s ON s.owner_id=x.id JOIN public.devices d ON d.owner_id=x.id AND d.id=s.device_id
      WHERE s.token_hash=p->>'token_hash' AND s.revoked_at IS NULL AND d.revoked_at IS NULL AND s.security_epoch=x.security_epoch AND now_at<least(s.idle_deadline,s.absolute_deadline);
  ELSIF p->>'lookup' IN('login_mfa','auto_recovery') THEN
    SELECT * INTO c FROM public.auth_challenges WHERE kind=p->>'lookup' AND consumed_at IS NULL AND now_at<expires_at
      AND ((kind='login_mfa' AND id=(p->>'challenge_id')::uuid AND parent_hash=p->>'preauth_hash') OR (kind='auto_recovery' AND token_hash=p->>'token_hash'));
    SELECT id,role,state,deleted_at,email_verified_at,password_hash,security_epoch INTO u FROM public.users WHERE id=c.owner_id AND security_epoch=c.security_epoch;
  ELSIF p->>'lookup'='reset' THEN
    SELECT x.id,x.role,x.state,x.deleted_at,x.email_verified_at,x.password_hash,x.security_epoch INTO u FROM public.users x JOIN public.email_actions a ON a.owner_id=x.id AND a.security_epoch=x.security_epoch
      WHERE a.token_hash=p->>'token_hash' AND a.purpose='reset' AND a.consumed_at IS NULL AND now_at<a.expires_at;
  ELSE RETURN NULL;END IF;
  IF u.id IS NULL OR u.state<>'active' OR u.deleted_at IS NOT NULL OR u.email_verified_at IS NULL OR u.password_hash IS NULL THEN RETURN NULL;END IF;
  IF p ? 'factor_id' THEN SELECT * INTO f FROM public.mfa_credentials WHERE id=(p->>'factor_id')::uuid AND owner_id=u.id AND state='pending' AND now_at<expires_at;
  ELSE SELECT * INTO f FROM public.mfa_credentials WHERE owner_id=u.id AND state='active';END IF;
  RETURN jsonb_build_object('owner_id',u.id,'role',u.role,'epoch',u.security_epoch,'password_hash',u.password_hash,'now',now_at,
    'factor_id',f.id,'encrypted_seed',f.encrypted_seed,'last_step',f.last_accepted_step,'scope',c.scope,'context_expires_at',c.expires_at);
END $f$;

CREATE FUNCTION identity_api.verify_auth(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE u record;f public.mfa_credentials%ROWTYPE;n timestamptz;mode text:=p->>'mode';level text:='none';
BEGIN
  IF mode NOT IN('login','login_mfa','reauth','automatic') THEN RETURN NULL;END IF;
  SELECT id,role,state,deleted_at,email_verified_at,password_hash,security_epoch INTO u FROM public.users WHERE id=(p->>'owner_id')::uuid FOR SHARE;
  IF u.id IS NULL OR u.state<>'active' OR u.deleted_at IS NOT NULL OR u.email_verified_at IS NULL OR u.security_epoch<>(p->>'epoch')::bigint OR u.password_hash IS DISTINCT FROM p->>'password_hash' THEN RETURN NULL;END IF;
  IF mode<>'login' AND mode<>'automatic' THEN
    SELECT * INTO f FROM public.mfa_credentials WHERE owner_id=u.id AND state='active' FOR UPDATE;
    IF f.id IS NOT NULL OR u.role='admin' THEN
      n:=clock_timestamp();
      IF f.id IS NULL OR f.id IS DISTINCT FROM (p->>'factor_id')::uuid OR f.encrypted_seed IS DISTINCT FROM p->>'encrypted_seed' OR NOT (p ? 'step')
        OR (p->>'step')::bigint<=f.last_accepted_step OR abs((p->>'step')::bigint-floor(extract(epoch FROM n)/30)::bigint)>1 THEN RETURN NULL;END IF;
      UPDATE public.mfa_credentials SET last_accepted_step=(p->>'step')::bigint WHERE id=f.id;level:='totp';
    END IF;
  END IF;
  n:=clock_timestamp();
  INSERT INTO public.auth_challenges(id,kind,owner_id,token_hash,security_epoch,expires_at,scope)
    VALUES((p->>'proof_id')::uuid,'verified',u.id,p->>'proof_hash',u.security_epoch,n+interval '120 seconds',
      jsonb_build_object('mode',mode,'mfa_level',level,'factor_id',f.id,'password_hash',u.password_hash,'verified_at',n,'binding',p->'binding'));
  RETURN jsonb_build_object('verified_at',n,'expires_at',n+interval '120 seconds');
END $f$;

CREATE FUNCTION identity_api.verify_factor(p jsonb) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
  SELECT identity_api.verify_auth(p||jsonb_build_object('mode','login_mfa'))
$f$;

CREATE FUNCTION identity_api.validate_session(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE s public.auth_sessions%ROWTYPE;u record;d record;n timestamptz;
BEGIN
  SELECT * INTO s FROM public.auth_sessions WHERE token_hash=p->>'token_hash';
  IF s.id IS NULL THEN RETURN NULL;END IF;
  SELECT id,role,state,security_epoch,deleted_at,email_verified_at,normalized_email,profile,revision,created_at,updated_at INTO u FROM public.users WHERE id=s.owner_id FOR SHARE;
  SELECT id,revoked_at INTO d FROM public.devices WHERE owner_id=u.id AND id=s.device_id FOR SHARE;
  SELECT * INTO s FROM public.auth_sessions WHERE id=s.id FOR UPDATE;
  n:=clock_timestamp();
  IF u.state<>'active' OR u.deleted_at IS NOT NULL OR u.email_verified_at IS NULL OR d.id IS NULL OR d.revoked_at IS NOT NULL OR s.revoked_at IS NOT NULL
    OR s.role<>u.role OR s.security_epoch<>u.security_epoch OR n>=least(s.idle_deadline,s.absolute_deadline)
    OR (u.role='admin' AND s.mfa_level<>'totp')
    OR (s.mfa_level='totp' AND NOT EXISTS(SELECT FROM public.mfa_credentials WHERE owner_id=u.id AND id=s.factor_id AND state='active')) THEN RETURN NULL;END IF;
  IF coalesce((p->>'touch')::boolean,false) THEN
    UPDATE public.auth_sessions SET idle_deadline=least(absolute_deadline,n+CASE WHEN role='admin' THEN interval '15 minutes' ELSE interval '14 days' END),updated_at=n WHERE id=s.id RETURNING * INTO s;
  END IF;
  RETURN jsonb_build_object('owner_id',u.id,'session_id',s.id,'device_id',s.device_id,'epoch',u.security_epoch,'role',u.role,'mfa_level',s.mfa_level,
    'expires_at',least(s.idle_deadline,s.absolute_deadline),'absolute_deadline',s.absolute_deadline,
    'user',jsonb_build_object('id',u.id,'role',u.role,'status','active','revision',u.revision,'created_at',u.created_at,'updated_at',u.updated_at,'email',u.normalized_email,
      'nickname',coalesce(u.profile->>'nickname',''),'school',coalesce(u.profile->>'school',''),'major',coalesce(u.profile->>'major',''),
      'mfa_enabled',EXISTS(SELECT FROM public.mfa_credentials WHERE owner_id=u.id AND state='active')));
END $f$;

CREATE FUNCTION identity_api.establish_session(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE a public.auth_challenges%ROWTYPE;u record;f record;c public.auth_challenges%ROWTYPE;pre public.auth_challenges%ROWTYPE;n timestamptz;sid uuid;did uuid;absolute_at timestamptz;idle_at timestamptz;
BEGIN
  SELECT * INTO a FROM public.auth_challenges WHERE kind='verified' AND token_hash=p->>'proof_hash';
  IF a.id IS NULL THEN RETURN NULL;END IF;
  SELECT id,role,state,deleted_at,email_verified_at,password_hash,security_epoch INTO u FROM public.users WHERE id=a.owner_id FOR SHARE;
  SELECT id INTO f FROM public.mfa_credentials WHERE owner_id=u.id AND state='active' FOR SHARE;
  SELECT * INTO pre FROM public.auth_challenges WHERE kind='preauth' AND token_hash=p->>'preauth_hash' FOR UPDATE;
  IF p ? 'challenge_id' THEN
    SELECT * INTO c FROM public.auth_challenges WHERE id=(p->>'challenge_id')::uuid AND kind='login_mfa' AND parent_hash=pre.token_hash FOR UPDATE;
  END IF;
  SELECT * INTO a FROM public.auth_challenges WHERE id=a.id FOR UPDATE;n:=clock_timestamp();
  IF u.id IS NULL OR u.state<>'active' OR u.deleted_at IS NOT NULL OR u.email_verified_at IS NULL OR u.security_epoch<>a.security_epoch
    OR a.scope->>'password_hash' IS DISTINCT FROM u.password_hash OR a.consumed_at IS NOT NULL OR n>=a.expires_at
    OR pre.id IS NULL OR pre.consumed_at IS NOT NULL OR n>=pre.expires_at THEN RETURN NULL;END IF;
  IF p ? 'challenge_id' THEN
    IF c.id IS NULL OR c.owner_id<>u.id OR c.security_epoch<>u.security_epoch OR c.consumed_at IS NOT NULL OR n>=c.expires_at
      OR c.scope->>'password_hash' IS DISTINCT FROM u.password_hash OR a.scope->>'mode'<>'login_mfa' OR a.scope->>'mfa_level'<>'totp' THEN RETURN NULL;END IF;
    UPDATE public.auth_challenges SET consumed_at=n WHERE id=c.id;
  ELSIF f.id IS NOT NULL OR u.role='admin' THEN
    IF f.id IS NULL THEN RETURN jsonb_build_object('error','MFA_REQUIRED');END IF;
    INSERT INTO public.auth_challenges(id,kind,owner_id,token_hash,parent_hash,security_epoch,expires_at,scope)
      VALUES((p->>'challenge_new_id')::uuid,'login_mfa',u.id,p->>'challenge_hash',pre.token_hash,u.security_epoch,n+interval '5 minutes',
        jsonb_build_object('password_hash',u.password_hash,'nonce',p->>'nonce','device_name',p->>'device_name','remember_me',(p->>'remember_me')::boolean));
    UPDATE public.auth_challenges SET consumed_at=n WHERE id=a.id;
    RETURN jsonb_build_object('state','mfa_required','challenge_id',p->>'challenge_new_id','receiver_challenge',p->>'nonce');
  ELSIF a.scope->>'mode'<>'login' THEN RETURN NULL;END IF;
  did:=(p->>'device_id')::uuid;sid:=(p->>'session_id')::uuid;
  absolute_at:=n+CASE WHEN u.role='admin' THEN interval '8 hours' ELSE interval '30 days' END;
  idle_at:=least(absolute_at,n+CASE WHEN u.role='admin' THEN interval '15 minutes' ELSE interval '14 days' END);
  INSERT INTO public.devices(id,owner_id,device_name) VALUES(did,u.id,left(p->>'device_name',100));
  INSERT INTO public.auth_sessions(id,owner_id,device_id,token_hash,logical_session_id,security_epoch,role,mfa_level,factor_id,idle_deadline,absolute_deadline)
    VALUES(sid,u.id,did,p->>'session_hash',sid,u.security_epoch,u.role,CASE WHEN f.id IS NULL THEN 'none' ELSE 'totp' END,f.id,idle_at,absolute_at);
  UPDATE public.auth_challenges SET consumed_at=n WHERE id IN(a.id,pre.id);
  INSERT INTO identity_control.identity_control_audit(id,actor_id,subject_id,action,target_id) VALUES((p->>'audit_id')::uuid,u.id,u.id,'login',sid);
  RETURN jsonb_build_object('state','authenticated','role',u.role,'expires_at',absolute_at);
END $f$;

CREATE FUNCTION identity_api.protocol_context(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE c public.auth_challenges%ROWTYPE;n timestamptz:=clock_timestamp();
BEGIN
  IF p->>'action'='create' THEN
    PERFORM pg_advisory_xact_lock(7303001);
    IF (SELECT count(*) FROM public.auth_challenges WHERE kind='preauth' AND consumed_at IS NULL AND clock_timestamp()<expires_at)>=200 THEN RETURN jsonb_build_object('error','RATE_LIMITED');END IF;
    INSERT INTO public.auth_challenges(id,kind,token_hash,expires_at) VALUES((p->>'id')::uuid,'preauth',p->>'token_hash',n+interval '5 minutes');
    RETURN jsonb_build_object('expires_at',n+interval '5 minutes');
  END IF;
  SELECT * INTO c FROM public.auth_challenges WHERE token_hash=p->>'token_hash' AND kind=p->>'kind' FOR UPDATE;
  n:=clock_timestamp();
  IF c.id IS NULL OR c.consumed_at IS NOT NULL OR n>=c.expires_at THEN RETURN NULL;END IF;
  IF c.owner_id IS NOT NULL AND NOT EXISTS(SELECT FROM public.users WHERE id=c.owner_id AND state='active' AND deleted_at IS NULL AND security_epoch=c.security_epoch) THEN RETURN NULL;END IF;
  -- Lets the holder of a pending automatic-recovery context give it up, so the browser
  -- is not pinned to a flow whose receiver key it has discarded.
  IF p->>'action'='abandon' THEN
    IF c.kind<>'auto_recovery' THEN RETURN NULL;END IF;
    UPDATE public.auth_challenges SET consumed_at=n WHERE id=c.id;
    RETURN jsonb_build_object('id',c.id);
  END IF;
  RETURN jsonb_build_object('expires_at',c.expires_at,'id',c.id,'scope',c.scope);
END $f$;

CREATE FUNCTION identity_api.revoke_session(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE s jsonb;
BEGIN
  s:=identity_api.validate_session(p);
  IF s IS NULL THEN RETURN NULL;END IF;
  UPDATE public.auth_sessions SET revoked_at=clock_timestamp() WHERE id=(s->>'session_id')::uuid;
  INSERT INTO identity_control.identity_control_audit(id,actor_id,subject_id,action,target_id)
    VALUES((p->>'audit_id')::uuid,(s->>'owner_id')::uuid,(s->>'owner_id')::uuid,'logout',(s->>'session_id')::uuid);
  RETURN '{}'::jsonb;
END $f$;

-- Function ownership and runtime grants are frozen after all function bodies.
CREATE FUNCTION identity_api.lock_account(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE uid uuid;
BEGIN
  SELECT owner_id INTO uid FROM public.auth_sessions WHERE token_hash=p->>'token_hash';
  IF uid IS NULL THEN RETURN NULL;END IF;
  PERFORM id FROM public.users WHERE id=uid FOR UPDATE;
  RETURN identity_api.validate_session(p);
END $f$;

CREATE FUNCTION identity_api.issue_reauth(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE s jsonb;a public.auth_challenges%ROWTYPE;n timestamptz;until_at timestamptz;purpose text:=p->>'purpose';risk_window interval;
BEGIN
  s:=identity_api.lock_account(p);IF s IS NULL THEN RETURN NULL;END IF;
  SELECT * INTO a FROM public.auth_challenges WHERE token_hash=p->>'proof_hash' AND kind='verified' AND owner_id=(s->>'owner_id')::uuid FOR UPDATE;
  n:=clock_timestamp();
  IF a.id IS NULL OR a.consumed_at IS NOT NULL OR n>=a.expires_at OR a.security_epoch<>(s->>'epoch')::bigint
    OR a.scope->>'mode'<>'reauth' OR a.scope->>'binding' IS DISTINCT FROM p->>'commitment' THEN RETURN NULL;END IF;
  IF purpose IN('logoutAll','revokeSession','revokeDevice','trustDevice') THEN risk_window:=CASE WHEN s->>'role'='admin' THEN interval '300 seconds' ELSE interval '600 seconds' END;
  ELSIF purpose IN('changePassword','requestEmailChange') THEN risk_window:=interval '300 seconds';
  ELSIF purpose IN('enrollMfa','confirmMfa') THEN
    risk_window:=CASE WHEN EXISTS(SELECT FROM public.mfa_credentials WHERE owner_id=a.owner_id AND state='active') THEN interval '120 seconds' ELSE interval '300 seconds' END;
  ELSIF purpose IN('disableMfa','rotateRecoveryCodes','deleteAccount') THEN risk_window:=interval '120 seconds';
  ELSE RETURN jsonb_build_object('error','SERVICE_DISABLED');END IF;
  IF p->>'target_kind'='account' AND (p->>'target_id')::uuid<>a.owner_id THEN RETURN NULL;END IF;
  IF purpose='revokeSession' AND NOT EXISTS(SELECT FROM public.auth_sessions WHERE owner_id=a.owner_id AND id=(p->>'target_id')::uuid AND id<>(s->>'session_id')::uuid) THEN RETURN NULL;END IF;
  IF purpose IN('revokeDevice','trustDevice') AND NOT EXISTS(SELECT FROM public.devices WHERE owner_id=a.owner_id AND id=(p->>'target_id')::uuid AND revoked_at IS NULL) THEN RETURN NULL;END IF;
  IF purpose='confirmMfa' AND NOT EXISTS(SELECT FROM public.mfa_credentials WHERE owner_id=a.owner_id AND id=(p->>'target_id')::uuid AND state='pending') THEN RETURN NULL;END IF;
  until_at:=least((a.scope->>'verified_at')::timestamptz+risk_window,(s->>'expires_at')::timestamptz);
  IF n>=until_at THEN RETURN NULL;END IF;
  INSERT INTO public.reauth_proofs(id,owner_id,session_id,device_id,security_epoch,token_hash,purpose,target_id,commitment,operation_key,verified_at,expires_at)
    VALUES((p->>'id')::uuid,a.owner_id,(s->>'session_id')::uuid,(s->>'device_id')::uuid,a.security_epoch,p->>'reauth_hash',purpose,(p->>'target_id')::uuid,p->>'commitment',(p->>'operation_key')::uuid,(a.scope->>'verified_at')::timestamptz,until_at);
  UPDATE public.auth_challenges SET consumed_at=n WHERE id=a.id;
  RETURN jsonb_build_object('expires_at',until_at);
END $f$;

CREATE FUNCTION identity_api.revoke_derived(uid uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE n timestamptz:=clock_timestamp();
BEGIN
  UPDATE public.auth_sessions SET revoked_at=n WHERE owner_id=uid AND revoked_at IS NULL;
  UPDATE public.auth_challenges SET consumed_at=n WHERE owner_id=uid AND consumed_at IS NULL;
  UPDATE public.reauth_proofs SET consumed_at=n WHERE owner_id=uid AND consumed_at IS NULL;
  UPDATE public.email_actions SET consumed_at=n WHERE owner_id=uid AND consumed_at IS NULL;
END $f$;

CREATE FUNCTION identity_api.apply_account_change(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE s jsonb;proof public.reauth_proofs%ROWTYPE;receipt public.preauth_receipts%ROWTYPE;f public.mfa_credentials%ROWTYPE;
  uid uuid;n timestamptz;action text:=p->>'action';target uuid:=(p->>'target_id')::uuid;gid uuid;g bigint;item jsonb;answer jsonb:='{}';secret boolean:=false;
BEGIN
  IF action='requestEmailChange' THEN PERFORM pg_advisory_xact_lock(7303002);END IF;
  s:=identity_api.lock_account(p);IF s IS NULL THEN RETURN NULL;END IF;uid:=(s->>'owner_id')::uuid;
  SELECT * INTO receipt FROM public.preauth_receipts WHERE context_hash=p->>'token_hash' AND operation=action AND operation_key=(p->>'operation_key')::uuid FOR UPDATE;
  IF FOUND THEN
    IF receipt.commitment<>p->>'commitment' THEN RETURN jsonb_build_object('error','IDEMPOTENCY_MISMATCH');END IF;
    IF receipt.secret THEN RETURN jsonb_build_object('error','SECRET_ALREADY_ISSUED');END IF;
    RETURN receipt.result;
  END IF;
  SELECT * INTO proof FROM public.reauth_proofs WHERE token_hash=p->>'reauth_hash' AND owner_id=uid FOR UPDATE;
  n:=clock_timestamp();
  IF proof.id IS NULL OR proof.session_id<>(s->>'session_id')::uuid OR proof.device_id<>(s->>'device_id')::uuid OR proof.security_epoch<>(s->>'epoch')::bigint
    OR proof.purpose<>action OR proof.target_id<>target OR proof.commitment IS DISTINCT FROM p->>'commitment' OR proof.operation_key<>(p->>'operation_key')::uuid
    OR proof.consumed_at IS NOT NULL OR n>=proof.expires_at THEN RETURN jsonb_build_object('error','REAUTH_REQUIRED');END IF;
  IF action IN('logoutAll','changePassword','requestEmailChange','enrollMfa','rotateRecoveryCodes','disableMfa') AND target<>uid THEN RETURN NULL;END IF;
  IF action='logoutAll' THEN
    PERFORM identity_api.revoke_derived(uid);
  ELSIF action='changePassword' THEN
    IF p->>'new_hash' NOT LIKE '$argon2id$v=19$m=65536,t=3,p=1$%' THEN RETURN NULL;END IF;
    UPDATE public.users SET password_hash=p->>'new_hash',security_epoch=security_epoch+1,revision=revision+1,updated_at=n WHERE id=uid;
    PERFORM identity_api.revoke_derived(uid);
  ELSIF action='requestEmailChange' THEN
    IF p->>'encrypted_mail' IS NULL THEN RETURN NULL;END IF;
    IF (SELECT count(*) FROM identity_control.authentication_mail_jobs WHERE state IN('queued','sending') AND expires_at>clock_timestamp())>=200 THEN RETURN jsonb_build_object('error','RATE_LIMITED');END IF;
    INSERT INTO public.email_actions(id,owner_id,purpose,token_hash,security_epoch,new_email,expires_at)
      VALUES((p->>'action_id')::uuid,uid,'change_email',p->>'action_hash',(s->>'epoch')::bigint,p->>'new_email',n+interval '30 minutes');
    INSERT INTO identity_control.authentication_mail_jobs(id,owner_id,action_id,encrypted_payload,expires_at)
      VALUES((p->>'action_id')::uuid,uid,(p->>'action_id')::uuid,p->>'encrypted_mail',n+interval '30 minutes');
  ELSIF action='revokeSession' THEN
    IF target=(s->>'session_id')::uuid THEN RETURN NULL;END IF;
    UPDATE public.auth_sessions SET revoked_at=n WHERE owner_id=uid AND id=target;
    IF NOT FOUND THEN RETURN NULL;END IF;
  ELSIF action='revokeDevice' THEN
    UPDATE public.devices SET revoked_at=n,key_epoch=key_epoch+1,revision=revision+1,updated_at=n WHERE owner_id=uid AND id=target;
    IF NOT FOUND THEN RETURN NULL;END IF;
    UPDATE public.auth_sessions SET revoked_at=n WHERE owner_id=uid AND device_id=target;
  ELSIF action='trustDevice' THEN RETURN jsonb_build_object('error','SERVICE_DISABLED');
  ELSIF action='enrollMfa' THEN
    IF s->>'role'='admin' AND NOT EXISTS(SELECT FROM public.mfa_credentials WHERE owner_id=uid AND state='active') THEN RETURN NULL;END IF;
    UPDATE public.mfa_credentials SET state='revoked',revoked_at=n WHERE owner_id=uid AND state='pending';
    INSERT INTO public.mfa_credentials(id,owner_id,state,encrypted_seed,expires_at)
      VALUES((p->>'factor_id')::uuid,uid,'pending',p->>'encrypted_seed',n+interval '5 minutes');
    answer:=jsonb_build_object('enrollment_id',p->>'factor_id','expires_at',n+interval '5 minutes');secret:=true;
  ELSIF action IN('confirmMfa','disableMfa','rotateRecoveryCodes') THEN
    IF action='disableMfa' AND s->>'role'='admin' THEN RETURN jsonb_build_object('error','FORBIDDEN');END IF;
    IF action='confirmMfa' THEN
      SELECT * INTO f FROM public.mfa_credentials WHERE owner_id=uid AND id=target AND state='pending' FOR UPDATE;n:=clock_timestamp();
      IF f.id IS NULL OR n>=least(f.expires_at,proof.expires_at) OR f.encrypted_seed IS DISTINCT FROM p->>'encrypted_seed'
        OR NOT (p ? 'step') OR (p->>'step')::bigint<=f.last_accepted_step OR abs((p->>'step')::bigint-floor(extract(epoch FROM n)/30)::bigint)>1 THEN RETURN NULL;END IF;
      UPDATE public.mfa_credentials SET state='revoked',revoked_at=n WHERE owner_id=uid AND state='active';
      UPDATE public.mfa_credentials SET state='active',confirmed_at=n,last_accepted_step=(p->>'step')::bigint WHERE id=f.id;
    ELSE SELECT * INTO f FROM public.mfa_credentials WHERE owner_id=uid AND state='active' FOR UPDATE;
      IF f.id IS NULL THEN RETURN NULL;END IF;
    END IF;
    UPDATE public.recovery_code_groups SET revoked_at=n WHERE owner_id=uid AND revoked_at IS NULL;
    IF action='disableMfa' THEN
      UPDATE public.mfa_credentials SET state='revoked',revoked_at=n WHERE id=f.id;
      UPDATE public.users SET security_epoch=security_epoch+1,revision=revision+1,updated_at=n WHERE id=uid;
      PERFORM identity_api.revoke_derived(uid);
    ELSE
      IF jsonb_typeof(p->'codes') IS DISTINCT FROM 'array' OR coalesce(jsonb_array_length(p->'codes'),0) NOT BETWEEN 1 AND 20 THEN RETURN NULL;END IF;
      SELECT coalesce(max(generation),0)+1 INTO g FROM public.recovery_code_groups WHERE owner_id=uid;gid:=(p->>'group_id')::uuid;
      INSERT INTO public.recovery_code_groups(id,owner_id,credential_id,generation,security_epoch) VALUES(gid,uid,f.id,g,(s->>'epoch')::bigint);
      FOR item IN SELECT value FROM jsonb_array_elements(p->'codes') LOOP
        INSERT INTO public.mfa_recovery_codes(id,owner_id,group_id,code_hash) VALUES((item->>'id')::uuid,uid,gid,item->>'hash');
      END LOOP;secret:=true;
      -- Factor changes revoke prior sessions; rotation itself does not log out.
      IF action='confirmMfa' THEN
        UPDATE public.users SET security_epoch=security_epoch+1,revision=revision+1,updated_at=n WHERE id=uid;
        UPDATE public.recovery_code_groups SET security_epoch=(s->>'epoch')::bigint+1 WHERE id=gid;
        PERFORM identity_api.revoke_derived(uid);
      END IF;
    END IF;
  ELSE RETURN jsonb_build_object('error','SERVICE_DISABLED');END IF;
  n:=clock_timestamp();
  IF n>=proof.expires_at THEN RAISE EXCEPTION 'authentication proof expired' USING ERRCODE='42501';END IF;
  UPDATE public.reauth_proofs SET consumed_at=n WHERE id=proof.id;
  INSERT INTO public.preauth_receipts(context_hash,operation,operation_key,commitment,secret,result)
    VALUES(p->>'token_hash',action,(p->>'operation_key')::uuid,p->>'commitment',secret,CASE WHEN secret THEN NULL ELSE answer END);
  INSERT INTO identity_control.identity_control_audit(id,actor_id,subject_id,action,target_id) VALUES((p->>'audit_id')::uuid,uid,uid,action,target);
  RETURN answer;
END $f$;

CREATE FUNCTION identity_api.manage_email_action(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE u record;a public.email_actions%ROWTYPE;n timestamptz;
BEGIN
  IF p->>'action'='forgot' THEN
    SELECT id,security_epoch INTO u FROM public.users WHERE normalized_email=p->>'email' AND state='active' AND deleted_at IS NULL AND email_verified_at IS NOT NULL FOR SHARE;
    IF u.id IS NULL THEN RETURN '{}'::jsonb;END IF;
    INSERT INTO public.email_actions(id,owner_id,purpose,token_hash,security_epoch,expires_at)
      VALUES((p->>'id')::uuid,u.id,'reset',p->>'action_hash',u.security_epoch,clock_timestamp()+interval '30 minutes');
    RETURN jsonb_build_object('delivery_owner',u.id);
  END IF;
  SELECT * INTO a FROM public.email_actions WHERE token_hash=p->>'action_hash';
  IF a.id IS NULL THEN RETURN NULL;END IF;
  SELECT id,role,security_epoch,state,deleted_at INTO u FROM public.users WHERE id=a.owner_id FOR UPDATE;
  SELECT * INTO a FROM public.email_actions WHERE id=a.id FOR UPDATE;n:=clock_timestamp();
  IF u.id IS NULL OR u.state<>'active' OR u.deleted_at IS NOT NULL OR u.security_epoch<>a.security_epoch OR a.consumed_at IS NOT NULL OR n>=a.expires_at THEN RETURN NULL;END IF;
  IF p->>'action'='reset' AND a.purpose='reset' THEN
    IF p->>'new_hash' NOT LIKE '$argon2id$v=19$m=65536,t=3,p=1$%' THEN RETURN NULL;END IF;
    UPDATE public.users SET password_hash=p->>'new_hash',security_epoch=security_epoch+1,revision=revision+1,updated_at=n WHERE id=u.id;
  ELSIF p->>'action'='confirm_email' AND a.purpose='change_email' THEN
    UPDATE public.users SET normalized_email=a.new_email,security_epoch=security_epoch+1,revision=revision+1,updated_at=n WHERE id=u.id;
  ELSE RETURN NULL;END IF;
  PERFORM identity_api.revoke_derived(u.id);
  INSERT INTO identity_control.identity_control_audit(id,subject_id,action,target_id) VALUES((p->>'audit_id')::uuid,u.id,p->>'action',a.id);
  RETURN '{}'::jsonb;
END $f$;

CREATE FUNCTION identity_api.list_security_context(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE s jsonb;rows jsonb;
BEGIN
  s:=identity_api.validate_session(p);IF s IS NULL THEN RETURN NULL;END IF;
  IF p->>'kind'='sessions' THEN
    -- The contract caps each list at 100 items. Every login adds a row, so bound the
    -- result: the current session first, then usable ones, then the most recent history.
    SELECT coalesce(jsonb_agg(x.item ORDER BY x.created_at DESC),'[]') INTO rows FROM (
      SELECT jsonb_build_object('id',a.id,'revision',a.revision,'created_at',a.created_at,'updated_at',a.updated_at,'device_id',a.device_id,
        'device_name',d.device_name,'last_active_at',a.updated_at,'expires_at',least(a.idle_deadline,a.absolute_deadline),'is_current',a.id=(s->>'session_id')::uuid,'revoked_at',a.revoked_at) AS item,a.created_at
      FROM public.auth_sessions a JOIN public.devices d ON d.owner_id=a.owner_id AND d.id=a.device_id WHERE a.owner_id=(s->>'owner_id')::uuid
      ORDER BY (a.id=(s->>'session_id')::uuid) DESC,(a.revoked_at IS NULL AND least(a.idle_deadline,a.absolute_deadline)>clock_timestamp()) DESC,a.created_at DESC,a.id LIMIT 100) x;
  ELSIF p->>'kind'='devices' THEN
    SELECT coalesce(jsonb_agg(x.item ORDER BY x.created_at DESC),'[]') INTO rows FROM (
      SELECT jsonb_build_object('id',d.id,'revision',d.revision,'created_at',d.created_at,'updated_at',d.updated_at,'device_name',d.device_name,
        'trusted',d.trusted,'revoked_at',d.revoked_at,'key_epoch',d.key_epoch) AS item,d.created_at
      FROM public.devices d WHERE d.owner_id=(s->>'owner_id')::uuid
      ORDER BY (d.id=(s->>'device_id')::uuid) DESC,(d.revoked_at IS NULL) DESC,d.created_at DESC,d.id LIMIT 100) x;
  ELSE RETURN NULL;END IF;
  RETURN jsonb_build_object('items',rows,'next_cursor',NULL);
END $f$;

CREATE FUNCTION identity_api.redeem_auto_recovery(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE c public.auth_challenges%ROWTYPE;a public.auth_challenges%ROWTYPE;f public.mfa_credentials%ROWTYPE;g public.recovery_code_groups%ROWTYPE;
  u record;n timestamptz;fid uuid;answer jsonb;code_id uuid;
BEGIN
  IF p->>'action'='begin' THEN
    SELECT * INTO c FROM public.auth_challenges WHERE id=(p->>'challenge_id')::uuid AND kind='login_mfa';
  ELSE SELECT * INTO c FROM public.auth_challenges WHERE token_hash=p->>'token_hash' AND kind='auto_recovery';END IF;
  IF c.id IS NULL THEN RETURN NULL;END IF;
  SELECT id,state,deleted_at,password_hash,security_epoch INTO u FROM public.users WHERE id=c.owner_id FOR UPDATE;
  SELECT * INTO f FROM public.mfa_credentials WHERE owner_id=u.id AND state='active' FOR UPDATE;
  SELECT * INTO c FROM public.auth_challenges WHERE id=c.id FOR UPDATE;n:=clock_timestamp();
  IF u.state<>'active' OR u.deleted_at IS NOT NULL OR c.security_epoch<>u.security_epoch OR c.consumed_at IS NOT NULL OR n>=c.expires_at
    OR c.scope->>'password_hash' IS DISTINCT FROM u.password_hash OR c.scope->>'nonce' IS DISTINCT FROM p->>'nonce' THEN RETURN NULL;END IF;
  IF p->>'action'='begin' THEN
    IF c.parent_hash IS DISTINCT FROM p->>'preauth_hash' OR NOT EXISTS(SELECT FROM public.auth_challenges WHERE kind='preauth' AND token_hash=c.parent_hash AND consumed_at IS NULL AND clock_timestamp()<expires_at) THEN RETURN NULL;END IF;
    SELECT * INTO g FROM public.recovery_code_groups WHERE owner_id=u.id AND credential_id=f.id AND security_epoch=u.security_epoch AND revoked_at IS NULL FOR UPDATE;
    SELECT id INTO code_id FROM public.mfa_recovery_codes WHERE owner_id=u.id AND group_id=g.id AND code_hash=p->>'code_hash' AND consumed_at IS NULL FOR UPDATE;
    n:=clock_timestamp();IF code_id IS NULL OR n>=c.expires_at THEN RETURN NULL;END IF;
    UPDATE public.mfa_recovery_codes SET consumed_at=n WHERE id=code_id;
    UPDATE public.auth_challenges SET consumed_at=n WHERE id=c.id;
    INSERT INTO public.auth_challenges(id,kind,owner_id,token_hash,security_epoch,expires_at,scope)
      VALUES((p->>'flow_id')::uuid,'auto_recovery',u.id,p->>'recovery_hash',u.security_epoch,n+interval '10 minutes',
        jsonb_build_object('password_hash',u.password_hash,'nonce',p->>'next_nonce','receiver',p->'receiver','generation',1));
    answer:=jsonb_build_object('flow_id',p->>'flow_id','expires_at',n+interval '10 minutes');
  ELSE
    IF c.id IS DISTINCT FROM (p->>'flow_id')::uuid THEN RETURN NULL;END IF;
    SELECT * INTO a FROM public.auth_challenges WHERE kind='verified' AND token_hash=p->>'proof_hash' AND owner_id=u.id FOR UPDATE;n:=clock_timestamp();
    IF a.id IS NULL OR a.consumed_at IS NOT NULL OR a.security_epoch<>u.security_epoch OR n>=least(a.expires_at,c.expires_at)
      OR a.scope->>'mode'<>'automatic' OR a.scope->>'binding' IS DISTINCT FROM p->>'binding' THEN RETURN NULL;END IF;
    IF EXISTS(SELECT FROM public.preauth_receipts WHERE context_hash=c.token_hash AND operation=p->>'action' AND operation_key=(p->>'operation_key')::uuid) THEN RETURN jsonb_build_object('error','SECRET_ALREADY_ISSUED');END IF;
    IF p->>'action'='enroll' THEN
      UPDATE public.mfa_credentials SET state='revoked',revoked_at=n WHERE owner_id=u.id AND state='pending';
      fid:=(p->>'factor_id')::uuid;
      INSERT INTO public.mfa_credentials(id,owner_id,state,encrypted_seed,expires_at) VALUES(fid,u.id,'pending',p->>'encrypted_seed',c.expires_at);
      UPDATE public.auth_challenges SET scope=scope||jsonb_build_object('factor_id',fid,'nonce',p->>'next_nonce','generation',(scope->>'generation')::bigint+1) WHERE id=c.id;
      answer:=jsonb_build_object('enrollment_id',fid,'expires_at',c.expires_at);
    ELSIF p->>'action'='confirm' THEN
      fid:=(p->>'factor_id')::uuid;
      SELECT * INTO f FROM public.mfa_credentials WHERE id=fid AND owner_id=u.id AND state='pending' FOR UPDATE;n:=clock_timestamp();
      IF f.id IS NULL OR c.scope->>'factor_id' IS DISTINCT FROM p->>'factor_id' OR f.encrypted_seed IS DISTINCT FROM p->>'encrypted_seed'
        OR n>=least(a.expires_at,c.expires_at,f.expires_at) OR NOT (p ? 'step') OR (p->>'step')::bigint IS NULL
        OR (p->>'step')::bigint<=f.last_accepted_step OR abs((p->>'step')::bigint-floor(extract(epoch FROM n)/30)::bigint)>1 THEN RETURN NULL;END IF;
      UPDATE public.mfa_credentials SET state='revoked',revoked_at=n WHERE owner_id=u.id AND state='active';
      UPDATE public.mfa_credentials SET state='active',confirmed_at=n,last_accepted_step=(p->>'step')::bigint WHERE id=f.id;
      UPDATE public.recovery_code_groups SET revoked_at=n WHERE owner_id=u.id AND revoked_at IS NULL;
      UPDATE public.users SET security_epoch=security_epoch+1,revision=revision+1,updated_at=n WHERE id=u.id;
      PERFORM identity_api.revoke_derived(u.id);
      answer:=jsonb_build_object('state','completed','flow_id',c.id,'requires_full_login',true,'new_recovery_codes_issued',false);
    ELSE RETURN NULL;END IF;
    UPDATE public.auth_challenges SET consumed_at=n WHERE id=a.id;
    INSERT INTO public.preauth_receipts(context_hash,operation,operation_key,commitment,secret) VALUES(c.token_hash,p->>'action',(p->>'operation_key')::uuid,p->>'binding',true);
  END IF;
  INSERT INTO identity_control.identity_control_audit(id,subject_id,action,target_id) VALUES((p->>'audit_id')::uuid,u.id,'automatic_'||(p->>'action'),c.id);
  RETURN answer;
END $f$;

CREATE FUNCTION identity_api.manage_ws_ticket(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE s jsonb;c public.auth_challenges%ROWTYPE;n timestamptz;until_at timestamptz;
BEGIN
  s:=identity_api.validate_session(p);IF s IS NULL THEN RETURN NULL;END IF;n:=clock_timestamp();
  IF p->>'action'='issue' THEN
    -- Capture/fence authority belongs to PR-04; never turn an auth ticket into
    -- permission to record or activate a provider.
    IF p->'scope'->>'channel'<>'events' OR p->'scope'->>'class_session_id' IS NOT NULL
      OR p->'scope'->>'capture_epoch_id' IS NOT NULL OR p->'scope'->>'fence_token' IS NOT NULL THEN RETURN jsonb_build_object('error','SERVICE_DISABLED');END IF;
    until_at:=least(n+interval '30 seconds',(s->>'expires_at')::timestamptz);
    INSERT INTO public.auth_challenges(id,kind,owner_id,token_hash,parent_hash,security_epoch,expires_at,scope)
      VALUES((p->>'id')::uuid,'ws',(s->>'owner_id')::uuid,p->>'ticket_hash',p->>'token_hash',(s->>'epoch')::bigint,until_at,p->'scope');
    RETURN jsonb_build_object('expires_at',until_at);
  ELSIF p->>'action'='consume' THEN
    SELECT * INTO c FROM public.auth_challenges WHERE kind='ws' AND token_hash=p->>'ticket_hash' FOR UPDATE;n:=clock_timestamp();
    IF c.id IS NULL OR c.consumed_at IS NOT NULL OR c.owner_id<>(s->>'owner_id')::uuid OR c.security_epoch<>(s->>'epoch')::bigint
      OR c.parent_hash IS DISTINCT FROM p->>'token_hash' OR n>=c.expires_at OR p->>'first_deadline' IS NULL OR n>=(p->>'first_deadline')::timestamptz
      OR c.scope->>'channel' IS DISTINCT FROM p->>'channel' THEN RETURN NULL;END IF;
    UPDATE public.auth_challenges SET consumed_at=n WHERE id=c.id;
    RETURN jsonb_build_object('owner_id',s->>'owner_id');
  ELSE RETURN NULL;END IF;
END $f$;

CREATE FUNCTION identity_api.authentication_budget(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE r identity_control.authentication_budgets%ROWTYPE;at_time timestamptz:=clock_timestamp();lim integer;seconds integer;
BEGIN
  CASE p->>'kind'
    WHEN 'preauth_source' THEN lim:=90;seconds:=60;
    WHEN 'preauth_global' THEN lim:=120;seconds:=60;
    WHEN 'account_fail' THEN lim:=3;seconds:=900;
    WHEN 'source_fail' THEN lim:=20;seconds:=60;
    WHEN 'mfa_fail' THEN lim:=5;seconds:=900;
    WHEN 'mail' THEN lim:=3;seconds:=3600;
    ELSE RETURN NULL;
  END CASE;
  INSERT INTO identity_control.authentication_budgets(budget_key,window_start,n) VALUES(p->>'key',at_time,0) ON CONFLICT DO NOTHING;
  SELECT * INTO r FROM identity_control.authentication_budgets WHERE budget_key=p->>'key' FOR UPDATE;
  IF at_time>=r.window_start+make_interval(secs=>seconds) THEN
    UPDATE identity_control.authentication_budgets SET n=0,window_start=at_time WHERE budget_key=r.budget_key;r.n:=0;r.window_start:=at_time;
  END IF;
  -- Returns one reservation taken by an earlier increment in the same window.
  -- Callers reserve before verifying a credential and release only when the
  -- attempt was not a credential failure, so concurrent attempts cannot all pass
  -- a check that precedes their increments.
  IF coalesce((p->>'release')::boolean,false) THEN
    IF r.n>0 AND p->>'window' IS NOT NULL AND r.window_start=(p->>'window')::timestamptz THEN
      UPDATE identity_control.authentication_budgets SET n=r.n-1 WHERE budget_key=r.budget_key;
    END IF;
    RETURN jsonb_build_object('allowed',true);
  END IF;
  IF r.n>=lim THEN RETURN jsonb_build_object('allowed',false);END IF;
  IF coalesce((p->>'increment')::boolean,false) THEN UPDATE identity_control.authentication_budgets SET n=r.n+1 WHERE budget_key=r.budget_key;END IF;
  RETURN jsonb_build_object('allowed',true,'window',r.window_start);
END $f$;

CREATE FUNCTION public.classroom_web_session_valid(uid uuid,sid uuid,did uuid,epoch bigint,expected_role text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE s public.auth_sessions%ROWTYPE;d record;n timestamptz;
BEGIN
  IF uid IS DISTINCT FROM nullif(current_setting('app.owner_id',true),'')::uuid THEN RETURN false;END IF;
  SELECT id,revoked_at INTO d FROM public.devices WHERE owner_id=uid AND id=did FOR SHARE;
  SELECT * INTO s FROM public.auth_sessions WHERE owner_id=uid AND id=sid AND device_id=did FOR SHARE;n:=clock_timestamp();
  RETURN coalesce(d.id IS NOT NULL AND d.revoked_at IS NULL AND s.id IS NOT NULL AND s.revoked_at IS NULL AND s.security_epoch=epoch AND s.role=expected_role
    AND n<least(s.idle_deadline,s.absolute_deadline)
    AND (s.mfa_level<>'totp' OR EXISTS(SELECT FROM public.mfa_credentials WHERE owner_id=uid AND id=s.factor_id AND state='active')),false);
END $f$;

-- Revoke every default EXECUTE, including internal helper functions. Functions
-- remain owned by separate non-login principals, not the table owner.
GRANT CREATE ON SCHEMA identity_api TO classroom_web_auth_verifier,classroom_web_session_writer;
GRANT CREATE ON SCHEMA public TO classroom_web_session_writer;
DO $functions$
DECLARE r record;who text;
BEGIN
  FOR r IN SELECT p.oid,p.proname,p.oid::regprocedure AS signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='identity_api' LOOP
    who:=CASE WHEN r.proname IN('locate_auth','verify_auth','verify_factor') THEN 'classroom_web_auth_verifier' ELSE 'classroom_web_session_writer' END;
    EXECUTE format('ALTER FUNCTION %s OWNER TO %I',r.signature,who);
    EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,classroom_web_runtime,classroom_web_identity,classroom_web_scheduler,classroom_web_task_dispatcher',r.signature);
  END LOOP;
END $functions$;
ALTER FUNCTION public.classroom_web_session_valid(uuid,uuid,uuid,bigint,text) OWNER TO classroom_web_session_writer;
REVOKE ALL ON FUNCTION public.classroom_web_session_valid(uuid,uuid,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.classroom_web_session_valid(uuid,uuid,uuid,bigint,text) TO classroom_web_runtime;
REVOKE CREATE ON SCHEMA identity_api FROM classroom_web_auth_verifier,classroom_web_session_writer;
REVOKE CREATE ON SCHEMA public FROM classroom_web_session_writer;
