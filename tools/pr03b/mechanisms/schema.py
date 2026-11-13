"""Fixed disposable SQL only. All objects lab3_*; no Alembic revision."""
from pathlib import Path
import common as b
base=b.load('_s3_foundation_schema',Path(__file__).resolve().parents[1]/'batch4/schema.py' if not b.BASE.exists() else b.BASE/'schema.py')
SERVICES=base.SERVICES;DENIALS=base.DENIALS;OWNERS=base.OWNERS
logins=base.logins;PASSWORD=base.PASSWORD
LOAD_ACTORS=(2,*range(100,129))
def ticket(service,actor=1):
    if actor in (1,2,3,4,5):return base.ticket(service,actor)
    b.require(service in ('session','runtime') and actor in LOAD_ACTORS,'fixed_load_actor_context')
    return f'B4-SYNTHETIC-{service}-{actor}'
def install(run):return base.install(run)
TABLES=('permissions','cases','ops','proofs','audit','notices','factors','groups','sessions','steps','budgets','business_counter','business_outbox','task_evidence','tickets','web_proofs','qualifications','hash_jobs')
def extension(run):
    roles=logins(run);q=base.ident;owner=q(base.TABLE_OWNER)
    sql='''
ALTER TABLE identity_control.lab_guard ADD COLUMN manifest_hash text NOT NULL DEFAULT repeat('0',64);
ALTER TABLE identity_control.lab_guard ADD COLUMN transition_id text;
CREATE TABLE identity_control.lab3_permissions(actor integer PRIMARY KEY REFERENCES identity_control.lab_principals(id), enabled boolean NOT NULL);
CREATE TABLE identity_control.lab3_qualifications(actor integer PRIMARY KEY REFERENCES identity_control.lab_principals(id),epoch integer NOT NULL,session_current boolean NOT NULL,device_current boolean NOT NULL,mfa_current boolean NOT NULL,os_key_current boolean NOT NULL,expires timestamptz NOT NULL);
CREATE TABLE identity_control.lab3_cases(id integer PRIMARY KEY,executor integer NOT NULL REFERENCES identity_control.lab_principals(id),target integer NOT NULL REFERENCES identity_control.lab_principals(id),target_epoch integer NOT NULL,
 state text NOT NULL CHECK(state IN ('open','pending','pending_confirmed','completed','abandoned')),flow integer NOT NULL,receiver text NOT NULL,expires timestamptz,cap_hash text,cap_used boolean NOT NULL DEFAULT false,completed_op integer,UNIQUE(id,executor));
CREATE TABLE identity_control.lab3_ops(id integer PRIMARY KEY,case_id integer NOT NULL,executor integer NOT NULL,action text NOT NULL,state text NOT NULL CHECK(state IN ('reserved','committed')),audit_id integer,
 FOREIGN KEY(case_id,executor) REFERENCES identity_control.lab3_cases(id,executor),UNIQUE(id,case_id,executor,action));
CREATE TABLE identity_control.lab3_proofs(id integer PRIMARY KEY,op integer UNIQUE NOT NULL REFERENCES identity_control.lab3_ops(id),case_id integer NOT NULL REFERENCES identity_control.lab3_cases(id),executor integer NOT NULL,
 action text NOT NULL,plan_hash text NOT NULL,receiver text NOT NULL,flow integer NOT NULL,verified_at timestamptz NOT NULL,challenge_expiry timestamptz NOT NULL,deadline timestamptz NOT NULL,used boolean NOT NULL DEFAULT false,guard_version integer NOT NULL,manifest_hash text NOT NULL,
 CHECK(verified_at<challenge_expiry),CHECK(deadline<=verified_at+interval '120 seconds'),
 FOREIGN KEY(op,case_id,executor,action) REFERENCES identity_control.lab3_ops(id,case_id,executor,action));
CREATE TABLE identity_control.lab3_audit(id integer PRIMARY KEY,op integer UNIQUE NOT NULL REFERENCES identity_control.lab3_ops(id),action text NOT NULL);
ALTER TABLE identity_control.lab3_ops ADD CONSTRAINT lab3_op_audit FOREIGN KEY(audit_id) REFERENCES identity_control.lab3_audit(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE identity_control.lab3_cases ADD CONSTRAINT lab3_case_op FOREIGN KEY(completed_op) REFERENCES identity_control.lab3_ops(id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE identity_control.lab3_notices(id integer PRIMARY KEY,subject integer NOT NULL REFERENCES identity_control.lab_principals(id),op integer UNIQUE NOT NULL REFERENCES identity_control.lab3_ops(id),lease integer NOT NULL DEFAULT 0,attempts integer NOT NULL DEFAULT 0,delivered boolean NOT NULL DEFAULT false);
CREATE TABLE identity_control.lab3_factors(owner integer NOT NULL REFERENCES identity_control.lab_principals(id),generation integer NOT NULL,state text NOT NULL,encrypted bytea NOT NULL,PRIMARY KEY(owner,generation));
CREATE UNIQUE INDEX lab3_one_active ON identity_control.lab3_factors(owner) WHERE state='active';
CREATE TABLE identity_control.lab3_groups(owner integer NOT NULL REFERENCES identity_control.lab_principals(id),generation integer NOT NULL,revoked boolean NOT NULL,shown boolean NOT NULL,op_key text NOT NULL,code_hashes text[] NOT NULL,PRIMARY KEY(owner,generation),UNIQUE(owner,op_key));
CREATE UNIQUE INDEX lab3_one_group ON identity_control.lab3_groups(owner) WHERE NOT revoked;
CREATE TABLE identity_control.lab3_sessions(id integer PRIMARY KEY,owner integer NOT NULL REFERENCES identity_control.lab_principals(id),epoch integer NOT NULL,revoked boolean NOT NULL,absolute_deadline timestamptz NOT NULL,idle_deadline timestamptz NOT NULL,token_hash text UNIQUE NOT NULL);
CREATE TABLE identity_control.lab3_steps(owner integer NOT NULL REFERENCES identity_control.lab_principals(id),credential integer NOT NULL,last_step bigint NOT NULL,PRIMARY KEY(owner,credential));
CREATE TABLE identity_control.lab3_tickets(token_hash text PRIMARY KEY,owner integer NOT NULL REFERENCES identity_control.lab_principals(id),session_id integer NOT NULL REFERENCES identity_control.lab3_sessions(id),epoch integer NOT NULL,expires timestamptz NOT NULL,used boolean NOT NULL DEFAULT false,classroom integer NOT NULL,fence integer NOT NULL);
CREATE TABLE identity_control.lab3_web_proofs(token_hash text PRIMARY KEY,owner integer NOT NULL REFERENCES identity_control.lab_principals(id),epoch integer NOT NULL,action text NOT NULL,binding text NOT NULL,deadline timestamptz NOT NULL,used boolean NOT NULL DEFAULT false);
CREATE TABLE identity_control.lab3_budgets(key text PRIMARY KEY,window_start timestamptz NOT NULL,n integer NOT NULL,not_before timestamptz NOT NULL);
CREATE TABLE identity_control.lab3_hash_jobs(id bigint PRIMARY KEY,worker integer NOT NULL,creation text NOT NULL,state text NOT NULL CHECK(state IN('active','queued')),queued_at timestamptz NOT NULL,deadline timestamptz NOT NULL);
CREATE TABLE identity_control.lab3_business_counter(owner integer PRIMARY KEY REFERENCES identity_control.lab_principals(id),n integer NOT NULL);
CREATE TABLE identity_control.lab3_business_outbox(id integer PRIMARY KEY,owner integer NOT NULL REFERENCES identity_control.lab_principals(id),payload text NOT NULL);
CREATE TABLE identity_control.lab3_task_evidence(id integer PRIMARY KEY,raw text NOT NULL,receipt text NOT NULL,outbox text NOT NULL,fence integer NOT NULL,unknown boolean NOT NULL,may_have_charged boolean NOT NULL,provider_calls integer NOT NULL);
INSERT INTO identity_control.lab3_permissions VALUES(3,true),(4,false),(5,true);
INSERT INTO identity_control.lab3_qualifications VALUES(3,1,true,true,true,false,clock_timestamp()+interval '4 hours'),(4,1,true,true,true,false,clock_timestamp()+interval '4 hours'),(5,1,false,false,false,true,clock_timestamp()+interval '4 hours');
INSERT INTO identity_control.lab3_factors VALUES(1,1,'active',decode('010203','hex')),(3,1,'active',decode('010203','hex'));
INSERT INTO identity_control.lab3_groups VALUES(1,1,false,true,'old-1',ARRAY['synthetic-digest']),(3,1,false,true,'old-3',ARRAY['synthetic-digest']);
INSERT INTO identity_control.lab3_steps VALUES(1,1,-1),(2,1,-1),(3,1,-1);
INSERT INTO identity_control.lab3_business_counter VALUES(1,0),(3,0);
INSERT INTO identity_control.lab3_task_evidence VALUES(1,'S3-RAW','S3-RECEIPT','S3-OUTBOX',7,true,true,0);
'''
    for table in TABLES:
        sql+=f'ALTER TABLE identity_control.lab3_{table} OWNER TO {owner}; ALTER TABLE identity_control.lab3_{table} ENABLE ROW LEVEL SECURITY; ALTER TABLE identity_control.lab3_{table} FORCE ROW LEVEL SECURITY; REVOKE ALL ON identity_control.lab3_{table} FROM PUBLIC;\n'
    # Runtime LOGIN has no table grants; each definer has explicit actions.
    grants={
      'recovery':{'permissions':'SELECT(actor,enabled), UPDATE(actor)','qualifications':'SELECT, UPDATE(actor)','cases':'SELECT, UPDATE(state,flow,expires,cap_hash,cap_used,completed_op)','ops':'SELECT, UPDATE(state,audit_id)','proofs':'SELECT, UPDATE(used)','audit':'INSERT','notices':'INSERT','factors':'SELECT, UPDATE(state)','groups':'SELECT, UPDATE(revoked)','sessions':'SELECT, UPDATE(revoked)'},
      'status':{'permissions':'SELECT(actor,enabled), UPDATE(actor)','qualifications':'SELECT, UPDATE(actor)','cases':'SELECT(id,executor,state,expires,flow,receiver), UPDATE(id)','ops':'SELECT(id,case_id,executor,action,state,audit_id), UPDATE(id)','proofs':'SELECT(op,case_id,executor,flow,receiver)'},
      'notice':{'notices':'SELECT, UPDATE(lease,attempts,delivered), DELETE'},
      'session':{'steps':'SELECT, UPDATE(last_step)','sessions':'SELECT, INSERT, UPDATE(idle_deadline,token_hash,revoked)','groups':'SELECT, INSERT, UPDATE(revoked)','budgets':'SELECT, INSERT, UPDATE','tickets':'SELECT, INSERT, UPDATE(used)','web_proofs':'SELECT, INSERT, UPDATE(used)'},
      'maintenance':{'permissions':'SELECT, UPDATE(enabled)','qualifications':'SELECT, UPDATE(os_key_current)'},
      'auth':{'hash_jobs':'SELECT, INSERT, UPDATE(state), DELETE','budgets':'SELECT, INSERT, UPDATE'},
    }
    for role,tables in grants.items():
        for table,actions in tables.items():
            sql+=f'GRANT {actions} ON identity_control.lab3_{table} TO {q(OWNERS[role])};\n'
            for action in ('SELECT','INSERT','UPDATE','DELETE'):
                if action not in actions:continue
                pred=f"identity_api.lab_actor('{role}') IS NOT NULL"
                using='' if action=='INSERT' else f'USING({pred})'
                check=f'WITH CHECK({"false" if role=="status" else pred})' if action in ('INSERT','UPDATE') else ''
                sql+=f'CREATE POLICY lab3_{role}_{table}_{action} ON identity_control.lab3_{table} FOR {action} TO {q(OWNERS[role])} {using} {check};\n'
    for role in ('recovery','status','session','maintenance'):
        sql+=f'GRANT SELECT(id,kind,active,epoch), UPDATE(id) ON identity_control.lab_principals TO {q(OWNERS[role])};\n'
        sql+=f"CREATE POLICY lab3_{role}_principals ON identity_control.lab_principals FOR SELECT TO {q(OWNERS[role])} USING(identity_api.lab_actor('{role}') IS NOT NULL);\n"
        sql+=f"CREATE POLICY lab3_{role}_principal_lock ON identity_control.lab_principals FOR UPDATE TO {q(OWNERS[role])} USING(identity_api.lab_actor('{role}') IS NOT NULL) WITH CHECK({'false' if role=='status' else 'true'});\n"
    sql+=f'GRANT UPDATE(epoch) ON identity_control.lab_principals TO {q(OWNERS["recovery"])};\n'
    sql+=f'GRANT SELECT(id,version,manifest_hash,transition_id), UPDATE(version,manifest_hash,transition_id) ON identity_control.lab_guard TO {q(OWNERS["maintenance"])};\n'
    for role in ('recovery','status'):
        sql+=f'GRANT SELECT(id,version,manifest_hash,transition_id), UPDATE(id) ON identity_control.lab_guard TO {q(OWNERS[role])};\n'
        sql+=f"CREATE POLICY lab3_{role}_guard ON identity_control.lab_guard FOR SELECT TO {q(OWNERS[role])} USING(identity_api.lab_actor('{role}') IS NOT NULL);\n"
        sql+=f"CREATE POLICY lab3_{role}_guard_lock ON identity_control.lab_guard FOR UPDATE TO {q(OWNERS[role])} USING(identity_api.lab_actor('{role}') IS NOT NULL) WITH CHECK(false);\n"
    def fn(name,args,returns,body,role):
        nonlocal sql
        signature=name+'('+','.join(a.strip().split()[1] for a in args.split(','))+')'
        sql+=f'''CREATE FUNCTION identity_api.{name}({args}) RETURNS {returns} LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $lab${body}$lab$;
ALTER FUNCTION identity_api.{signature} OWNER TO {q(OWNERS[role])}; REVOKE ALL ON FUNCTION identity_api.{signature} FROM PUBLIC;
GRANT EXECUTE ON FUNCTION identity_api.{signature} TO {q(roles[role])};\n'''
    fn('lab3_write','t text,pid integer,ph text,ch text','jsonb',r'''
DECLARE a integer;c identity_control.lab3_cases; p identity_control.lab3_proofs;current_epoch integer;now_at timestamptz;
BEGIN
 PERFORM set_config('batch4.ticket',t,true);a:=identity_api.lab_actor('recovery');
 IF a IS NULL THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
 SELECT * INTO p FROM identity_control.lab3_proofs WHERE id=pid;
 SELECT * INTO c FROM identity_control.lab3_cases WHERE id=p.case_id;
 PERFORM id FROM identity_control.lab_guard WHERE id=1 FOR SHARE;
 PERFORM id FROM identity_control.lab_principals WHERE id IN (a,c.target) ORDER BY id FOR UPDATE;
 PERFORM owner FROM identity_control.lab3_sessions WHERE owner=c.target ORDER BY id FOR UPDATE;
 PERFORM q.actor FROM identity_control.lab3_qualifications q JOIN identity_control.lab_principals u ON u.id=q.actor WHERE q.actor=a AND q.epoch=u.epoch AND clock_timestamp()<q.expires AND ((u.kind='admin' AND q.session_current AND q.device_current AND q.mfa_current) OR (u.kind='synthetic_authority' AND q.os_key_current)) FOR SHARE OF q;
 IF NOT FOUND THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
 PERFORM actor FROM identity_control.lab3_permissions WHERE actor=a AND enabled FOR SHARE;
 IF NOT FOUND OR c.executor<>a OR NOT EXISTS(SELECT 1 FROM identity_control.lab_principals WHERE id=c.target AND active) OR (a=3 AND (SELECT kind FROM identity_control.lab_principals WHERE id=c.target)<>'student') OR (a=5 AND (SELECT kind FROM identity_control.lab_principals WHERE id=c.target)<>'admin') OR a NOT IN(3,5) THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
 SELECT * INTO c FROM identity_control.lab3_cases WHERE id=c.id FOR UPDATE;
 PERFORM id FROM identity_control.lab3_ops WHERE id=p.op FOR UPDATE;
 SELECT * INTO p FROM identity_control.lab3_proofs WHERE id=pid FOR UPDATE;
 IF p.action='complete' THEN
   PERFORM owner FROM identity_control.lab3_factors WHERE owner=c.target ORDER BY generation FOR UPDATE;
   PERFORM owner FROM identity_control.lab3_groups WHERE owner=c.target ORDER BY generation FOR UPDATE;
 END IF;
 now_at:=clock_timestamp();SELECT epoch INTO current_epoch FROM identity_control.lab_principals WHERE id=c.target;
 IF NOT EXISTS(SELECT 1 FROM identity_control.lab3_qualifications q JOIN identity_control.lab_principals u ON u.id=q.actor WHERE q.actor=a AND q.epoch=u.epoch AND now_at<q.expires AND ((u.kind='admin' AND q.session_current AND q.device_current AND q.mfa_current) OR (u.kind='synthetic_authority' AND q.os_key_current))) THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
 IF NOT EXISTS(SELECT 1 FROM identity_control.lab_guard WHERE id=1 AND version=p.guard_version AND manifest_hash=p.manifest_hash) THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
 IF p.id IS NULL OR p.executor<>a OR p.case_id<>c.id OR p.used OR p.plan_hash<>ph OR p.receiver<>c.receiver OR p.flow<>c.flow OR now_at<p.verified_at OR now_at>=p.deadline OR current_epoch<>c.target_epoch OR c.state IN('completed','abandoned') OR (c.expires IS NOT NULL AND now_at>=c.expires) THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
 IF p.action='issue' AND c.state='open' THEN
   UPDATE identity_control.lab3_cases SET state='pending',expires=now_at+interval '600 seconds',cap_hash=ch WHERE id=c.id;
 ELSIF p.action='complete' AND c.state='pending_confirmed' AND NOT c.cap_used AND c.cap_hash=ch THEN
   UPDATE identity_control.lab3_factors SET state='revoked' WHERE owner=c.target AND state='active';
   UPDATE identity_control.lab3_factors SET state='active' WHERE owner=c.target AND generation=c.id;
   IF NOT FOUND THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
   UPDATE identity_control.lab3_groups SET revoked=true WHERE owner=c.target;
   UPDATE identity_control.lab_principals SET epoch=epoch+1 WHERE id=c.target;
   UPDATE identity_control.lab3_sessions SET revoked=true WHERE owner=c.target;
   UPDATE identity_control.lab3_cases SET state='completed',cap_used=true,completed_op=p.op WHERE id=c.id;
   INSERT INTO identity_control.lab3_notices(id,subject,op) VALUES(p.op,c.target,p.op);
 ELSE RAISE EXCEPTION 'denied' USING ERRCODE='42501'; END IF;
 UPDATE identity_control.lab3_proofs SET used=true WHERE id=p.id;
 INSERT INTO identity_control.lab3_audit VALUES(p.op,p.op,p.action);
 UPDATE identity_control.lab3_ops SET state='committed',audit_id=p.op WHERE id=p.op;
 RETURN jsonb_build_object('committed',true,'action',p.action,'new_recovery_codes_issued',false);
END;''','recovery')
    fn('lab3_status','t text,cid integer,oid integer,until_at timestamptz','jsonb',r'''
DECLARE a integer;c record;o record;n timestamptz;answer jsonb;
BEGIN
 PERFORM set_config('batch4.ticket',t,true);a:=identity_api.lab_actor('status');
 IF a IS NULL OR clock_timestamp()>=until_at OR until_at>clock_timestamp()+interval '120 seconds' THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
 PERFORM id FROM identity_control.lab_guard WHERE id=1 FOR SHARE;
 PERFORM id FROM identity_control.lab_principals WHERE id=a FOR SHARE;
 PERFORM q.actor FROM identity_control.lab3_qualifications q JOIN identity_control.lab_principals u ON u.id=q.actor WHERE q.actor=a AND q.epoch=u.epoch AND clock_timestamp()<q.expires AND ((u.kind='admin' AND q.session_current AND q.device_current AND q.mfa_current) OR (u.kind='synthetic_authority' AND q.os_key_current)) FOR SHARE OF q;
 IF NOT FOUND THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
 PERFORM actor FROM identity_control.lab3_permissions WHERE actor=a AND enabled FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
 SELECT id,executor,state,expires,flow,receiver INTO c FROM identity_control.lab3_cases WHERE id=cid AND executor=a FOR SHARE;
 SELECT id,state,audit_id INTO o FROM identity_control.lab3_ops WHERE id=oid AND case_id=cid AND executor=a FOR SHARE;
 n:=clock_timestamp();IF n>=until_at OR NOT EXISTS(SELECT 1 FROM identity_control.lab3_qualifications q JOIN identity_control.lab_principals u ON u.id=q.actor WHERE q.actor=a AND q.epoch=u.epoch AND n<q.expires AND ((u.kind='admin' AND q.session_current AND q.device_current AND q.mfa_current) OR (u.kind='synthetic_authority' AND q.os_key_current))) THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
 IF c.id IS NULL OR o.id IS NULL THEN answer:=jsonb_build_object('result','unknown','stage',null,'audit_id',null);
 ELSIF o.state='committed' THEN answer:=jsonb_build_object('result','committed','stage',c.state,'audit_id',o.audit_id);
 ELSIF c.state IN('pending','pending_confirmed') AND n<c.expires AND EXISTS(SELECT 1 FROM identity_control.lab3_proofs p WHERE p.op=oid AND p.case_id=cid AND p.executor=a AND p.flow=c.flow AND p.receiver=c.receiver) THEN answer:=jsonb_build_object('result','not_committed','stage',c.state,'audit_id',null);
 ELSE answer:=jsonb_build_object('result','unknown','stage',null,'audit_id',null);END IF;
 n:=clock_timestamp();IF n>=until_at OR NOT EXISTS(SELECT 1 FROM identity_control.lab3_qualifications q JOIN identity_control.lab_principals u ON u.id=q.actor WHERE q.actor=a AND q.epoch=u.epoch AND n<q.expires AND ((u.kind='admin' AND q.session_current AND q.device_current AND q.mfa_current) OR (u.kind='synthetic_authority' AND q.os_key_current))) THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
 IF answer->>'result'='not_committed' AND n>=c.expires THEN answer:=jsonb_build_object('result','unknown','stage',null,'audit_id',null);END IF;
 RETURN answer;
END;''','status')
    fn('lab3_mfa_step','t text,st bigint,until_at timestamptz','timestamptz',r'''
DECLARE a integer;n timestamptz;old bigint;
BEGIN PERFORM set_config('batch4.ticket',t,true);a:=identity_api.lab_actor('session');
PERFORM id FROM identity_control.lab_principals WHERE id=a AND active FOR SHARE;
IF NOT FOUND THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
SELECT last_step INTO old FROM identity_control.lab3_steps WHERE owner=a AND credential=1 FOR UPDATE;
n:=clock_timestamp();
IF old IS NULL OR st<=old OR until_at IS NULL OR n>=until_at THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
UPDATE identity_control.lab3_steps SET last_step=st WHERE owner=a AND credential=1;
RETURN n;END;''','session')
    fn('lab3_step','t text,st bigint','boolean',r'''
DECLARE a integer;BEGIN PERFORM set_config('batch4.ticket',t,true);a:=identity_api.lab_actor('session');
PERFORM id FROM identity_control.lab_principals WHERE id=a FOR SHARE;
UPDATE identity_control.lab3_steps SET last_step=st WHERE owner=a AND credential=1 AND last_step<st;
RETURN FOUND;END;''','session')
    fn('lab3_notice','t text,nid integer,mode text,token integer','boolean',r'''
BEGIN PERFORM set_config('batch4.ticket',t,true);
IF identity_api.lab_actor('notice')<>5 THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
IF mode='claim' THEN UPDATE identity_control.lab3_notices SET lease=token,attempts=attempts+1 WHERE id=nid AND lease=0 AND NOT delivered;
ELSIF mode='ack' THEN UPDATE identity_control.lab3_notices SET delivered=true,lease=0 WHERE id=nid AND lease=token;
ELSIF mode='retry' THEN UPDATE identity_control.lab3_notices SET lease=0 WHERE id=nid AND lease=token;
ELSIF mode='cleanup' THEN DELETE FROM identity_control.lab3_notices WHERE id=nid AND delivered AND lease=0;
ELSE RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;RETURN FOUND;END;''','notice')
    fn('lab3_session','t text,mode text,sid integer,th text,oldhash text','jsonb',r'''
DECLARE a integer;u record;s record;n timestamptz;
BEGIN PERFORM set_config('batch4.ticket',t,true);a:=identity_api.lab_actor('session');
 SELECT id,kind,active,epoch INTO u FROM identity_control.lab_principals WHERE id=a FOR SHARE;
 IF u.id IS NULL OR NOT u.active THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
 IF mode='establish' THEN
  n:=clock_timestamp();INSERT INTO identity_control.lab3_sessions VALUES(sid,a,u.epoch,false,n+CASE WHEN u.kind='admin' THEN interval '8 hours' ELSE interval '30 days' END,n+CASE WHEN u.kind='admin' THEN interval '15 minutes' ELSE interval '14 days' END,th);
 ELSE
  SELECT * INTO s FROM identity_control.lab3_sessions WHERE id=sid AND owner=a FOR UPDATE;
  n:=clock_timestamp();IF s.id IS NULL OR s.token_hash<>oldhash OR s.revoked OR s.epoch<>u.epoch OR n>=s.absolute_deadline OR n>=s.idle_deadline THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
  IF mode='rotate' THEN UPDATE identity_control.lab3_sessions SET token_hash=th WHERE id=sid;
  ELSIF mode='touch' THEN UPDATE identity_control.lab3_sessions SET idle_deadline=least(absolute_deadline,n+CASE WHEN u.kind='admin' THEN interval '15 minutes' ELSE interval '14 days' END) WHERE id=sid;
  ELSIF mode='revoke' THEN UPDATE identity_control.lab3_sessions SET revoked=true WHERE id=sid;
  ELSIF mode<>'validate' THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
 END IF;
 RETURN jsonb_build_object('owner',a,'epoch',u.epoch,'role',u.kind);
END;''','session')
    fn('lab3_ticket','t text,mode text,th text,sid integer,ep integer','boolean',r'''
DECLARE a integer;r record;s record;n timestamptz;
BEGIN PERFORM set_config('batch4.ticket',t,true);a:=identity_api.lab_actor('session');
 PERFORM id FROM identity_control.lab_principals WHERE id=a AND active AND epoch=ep FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
 SELECT * INTO s FROM identity_control.lab3_sessions WHERE id=sid AND owner=a FOR SHARE;
 n:=clock_timestamp();IF s.id IS NULL OR s.revoked OR s.epoch<>ep OR n>=s.absolute_deadline OR n>=s.idle_deadline THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
 IF mode='issue' THEN INSERT INTO identity_control.lab3_tickets VALUES(th,a,sid,ep,least(n+interval '30 seconds',s.absolute_deadline,s.idle_deadline),false,10,7);RETURN true;
 ELSIF mode='consume' THEN UPDATE identity_control.lab3_tickets SET used=true WHERE token_hash=th AND owner=a AND session_id=sid AND epoch=ep AND NOT used AND clock_timestamp()<expires AND classroom=10 AND fence=7;RETURN FOUND;
 ELSE RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;END;''','session')
    fn('lab3_web_proof','t text,ph text,bind text,ep integer,until_at timestamptz','boolean',r'''
DECLARE a integer;BEGIN PERFORM set_config('batch4.ticket',t,true);a:=identity_api.lab_actor('session');
PERFORM id FROM identity_control.lab_principals WHERE id=a AND active AND epoch=ep FOR SHARE;
IF NOT FOUND THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
IF clock_timestamp()>=until_at THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
INSERT INTO identity_control.lab3_web_proofs VALUES(ph,a,ep,'rotateRecoveryCodes',bind,least(until_at,clock_timestamp()+interval '120 seconds'),false);RETURN true;END;''','session')
    fn('lab3_codes','t text,ph text,bind text,opkey text,hashes text[]','jsonb',r'''
DECLARE a integer;p record;ep integer;gen integer;
BEGIN PERFORM set_config('batch4.ticket',t,true);a:=identity_api.lab_actor('session');
SELECT epoch INTO ep FROM identity_control.lab_principals WHERE id=a AND active FOR UPDATE;
IF ep IS NULL THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
SELECT * INTO p FROM identity_control.lab3_web_proofs WHERE token_hash=ph AND owner=a FOR UPDATE;
IF EXISTS(SELECT 1 FROM identity_control.lab3_groups WHERE owner=a AND op_key=opkey) THEN RETURN jsonb_build_object('secret_already_issued',true);END IF;
IF p.owner IS NULL OR p.epoch<>ep OR p.binding<>bind OR p.used OR clock_timestamp()>=p.deadline OR cardinality(hashes)<>10 THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
UPDATE identity_control.lab3_web_proofs SET used=true WHERE token_hash=ph;
PERFORM owner FROM identity_control.lab3_groups WHERE owner=a ORDER BY generation FOR UPDATE;
SELECT coalesce(max(generation),0)+1 INTO gen FROM identity_control.lab3_groups WHERE owner=a;
UPDATE identity_control.lab3_groups SET revoked=true WHERE owner=a;
INSERT INTO identity_control.lab3_groups VALUES(a,gen,false,true,opkey,hashes);
RETURN jsonb_build_object('generation',gen,'secret_already_issued',false);END;''','session')
    fn('lab3_hash_admission','t text,mode text,jid bigint,wid integer,ct text,until_at timestamptz DEFAULT NULL','jsonb',r'''
DECLARE r record;busy integer;q integer;result text;n timestamptz;reason text;
BEGIN PERFORM set_config('batch4.ticket',t,true);
IF identity_api.lab_actor('auth')<>2 THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
PERFORM pg_advisory_xact_lock(938448889);n:=clock_timestamp();
DELETE FROM identity_control.lab3_hash_jobs WHERE state='queued' AND deadline<=n;
SELECT count(*) FILTER(WHERE state='active'),count(*) FILTER(WHERE state='queued') INTO busy,q FROM identity_control.lab3_hash_jobs;
SELECT * INTO r FROM identity_control.lab3_hash_jobs WHERE id=jid FOR UPDATE;
IF mode='finish' THEN
 IF r.id IS NULL OR r.worker<>wid OR r.creation<>ct OR r.state<>'active' THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
 DELETE FROM identity_control.lab3_hash_jobs WHERE id=jid;result:='finished';
ELSIF mode='cancel' THEN
 IF r.id IS NOT NULL AND (r.worker<>wid OR r.creation<>ct OR r.state<>'queued') THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
 DELETE FROM identity_control.lab3_hash_jobs WHERE id=jid AND state='queued';result:='cancelled';
ELSIF mode IN('enter','poll') THEN
 IF r.id IS NOT NULL AND (r.worker<>wid OR r.creation<>ct) THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
 IF r.id IS NULL THEN
  IF mode<>'enter' THEN result:='rejected';reason:='queue_expired';
  ELSIF until_at IS NOT NULL AND n>=until_at THEN result:='rejected';reason:='ingress_expired';
  ELSIF busy<2 AND q=0 THEN INSERT INTO identity_control.lab3_hash_jobs VALUES(jid,wid,ct,'active',n,least(n+interval '1 second',until_at));result:='active';
  ELSIF q<8 THEN INSERT INTO identity_control.lab3_hash_jobs VALUES(jid,wid,ct,'queued',n,least(n+interval '1 second',until_at));result:='queued';
  ELSE result:='rejected';reason:='queue_full';END IF;
 ELSIF r.state='active' THEN result:='active';
 ELSIF clock_timestamp()>=r.deadline THEN DELETE FROM identity_control.lab3_hash_jobs WHERE id=jid;result:='rejected';
 ELSIF busy<2 AND NOT EXISTS(SELECT 1 FROM identity_control.lab3_hash_jobs WHERE state='queued' AND (queued_at,id)<(r.queued_at,r.id)) THEN UPDATE identity_control.lab3_hash_jobs SET state='active' WHERE id=jid;result:='active';
 ELSE result:='queued';END IF;
ELSE RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
SELECT count(*) FILTER(WHERE state='active'),count(*) FILTER(WHERE state='queued') INTO busy,q FROM identity_control.lab3_hash_jobs;
RETURN jsonb_build_object('state',result,'active',busy,'queued',q,'reason',reason);END;''','auth')
    fn('lab3_fail_budget','t text,k text,limit_count integer,window_seconds integer,increment boolean','jsonb',r'''
DECLARE r record;now_at timestamptz;BEGIN PERFORM set_config('batch4.ticket',t,true);
IF identity_api.lab_actor('auth')<>2 OR length(k)>128 OR limit_count NOT IN(3,5,10,20,90,120) OR window_seconds NOT IN(60,900,1800,3600) THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
now_at:=clock_timestamp();INSERT INTO identity_control.lab3_budgets VALUES(k,now_at,0,now_at) ON CONFLICT(key) DO NOTHING;
SELECT * INTO r FROM identity_control.lab3_budgets WHERE key=k FOR UPDATE;
IF now_at>=r.window_start+window_seconds*interval '1 second' THEN UPDATE identity_control.lab3_budgets SET n=0,window_start=now_at,not_before=now_at WHERE key=k; r.n:=0;r.not_before:=now_at;END IF;
IF r.n>=limit_count OR now_at<r.not_before THEN RETURN jsonb_build_object('allowed',false,'n',r.n);END IF;
IF increment THEN UPDATE identity_control.lab3_budgets SET n=lab3_budgets.n+1 WHERE key=k;END IF;
RETURN jsonb_build_object('allowed',true,'n',r.n+CASE WHEN increment THEN 1 ELSE 0 END);END;''','auth')
    fn('lab3_registration','t text,ov integer,oh text,nv integer,nh text,tr text,en boolean','boolean',r'''
BEGIN PERFORM set_config('batch4.ticket',t,true);
IF identity_api.lab_actor('maintenance')<>5 OR nv<=ov OR length(nh)<>64 OR length(tr)<16 THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
PERFORM id FROM identity_control.lab_guard WHERE id=1 FOR UPDATE;
PERFORM id FROM identity_control.lab_principals WHERE id=5 FOR SHARE;
UPDATE identity_control.lab_guard SET version=nv,manifest_hash=nh,transition_id=tr WHERE id=1 AND version=ov AND manifest_hash=oh;
IF NOT FOUND THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
PERFORM actor FROM identity_control.lab3_qualifications WHERE actor=5 FOR UPDATE;
PERFORM actor FROM identity_control.lab3_permissions WHERE actor=5 FOR UPDATE;
UPDATE identity_control.lab3_qualifications SET os_key_current=en WHERE actor=5;
UPDATE identity_control.lab3_permissions SET enabled=en WHERE actor=5;
RETURN true;END;''','maintenance')
    return sql

def login_extension(run):
    # One AUTH transaction combines only related fixed lookup/admission calls.
    # The native ownership checks still surround every complete transaction.
    q=base.ident;owner=q(OWNERS['auth']);login=q(logins(run)['auth'])
    return f'''
CREATE FUNCTION identity_api.lab3_login_admit(t text,account_key text,ns text,email_key text,jid bigint,wid integer,ct text,until_at timestamptz) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,pg_temp AS $lab$
DECLARE credential jsonb;admitted jsonb;
BEGIN
IF NOT (identity_api.lab3_fail_budget(t,ns||':'||account_key,3,900,false)->>'allowed')::boolean OR NOT (identity_api.lab3_fail_budget(t,ns||':login-failed-IP',20,60,false)->>'allowed')::boolean THEN RETURN jsonb_build_object('budget_allowed',false);END IF;
credential:=identity_api.lab3_lookup(t,email_key);
admitted:=identity_api.lab3_hash_admission(t,'enter',jid,wid,ct,until_at);
RETURN jsonb_build_object('budget_allowed',true,'credential',credential,'admission',admitted);
END;$lab$;
ALTER FUNCTION identity_api.lab3_login_admit(text,text,text,text,bigint,integer,text,timestamptz) OWNER TO {owner};
REVOKE ALL ON FUNCTION identity_api.lab3_login_admit(text,text,text,text,bigint,integer,text,timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION identity_api.lab3_login_admit(text,text,text,text,bigint,integer,text,timestamptz) TO {login};
CREATE FUNCTION identity_api.lab3_login_finish(t text,account_key text,ns text,jid bigint,wid integer,ct text,failed boolean) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,pg_temp AS $lab$
BEGIN
PERFORM identity_api.lab3_hash_admission(t,'finish',jid,wid,ct,NULL);
IF failed THEN PERFORM identity_api.lab3_fail_budget(t,ns||':'||account_key,3,900,true);PERFORM identity_api.lab3_fail_budget(t,ns||':login-failed-IP',20,60,true);END IF;
RETURN true;
END;$lab$;
ALTER FUNCTION identity_api.lab3_login_finish(text,text,text,bigint,integer,text,boolean) OWNER TO {owner};
REVOKE ALL ON FUNCTION identity_api.lab3_login_finish(text,text,text,bigint,integer,text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION identity_api.lab3_login_finish(text,text,text,bigint,integer,text,boolean) TO {login};
CREATE FUNCTION identity_api.lab3_preauth(t text,ns text) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,pg_temp AS $lab$
BEGIN
RETURN (identity_api.lab3_fail_budget(t,ns||':preauth-source-loopback',90,60,true)->>'allowed')::boolean AND (identity_api.lab3_fail_budget(t,ns||':preauth-global',120,60,true)->>'allowed')::boolean;
END;$lab$;
ALTER FUNCTION identity_api.lab3_preauth(text,text) OWNER TO {owner};
REVOKE ALL ON FUNCTION identity_api.lab3_preauth(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION identity_api.lab3_preauth(text,text) TO {login};
'''
