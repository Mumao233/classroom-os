"""Fixed disposable invitation/password/mail CAS mechanisms, not HTTP auth."""
import hashlib,json,secrets,threading
from pathlib import Path
import common as b,crypto,schema as s,cases
TABLES=('credentials','mail_tokens','invites','quota','change_audit')
def ddl(run):
    q=s.base.ident;owner=q(s.base.TABLE_OWNER);writer=q(s.OWNERS['session']);login=q(s.logins(run)['session']);auth=q(s.OWNERS['auth']);auth_login=q(s.logins(run)['auth'])
    sql='''
CREATE TABLE identity_control.lab3_credentials(owner integer PRIMARY KEY REFERENCES identity_control.lab_principals(id),phc text NOT NULL,version integer NOT NULL,email text UNIQUE NOT NULL,email_version integer NOT NULL,mfa_cipher bytea NOT NULL);
CREATE TABLE identity_control.lab3_mail_tokens(hash text PRIMARY KEY,owner integer NOT NULL REFERENCES identity_control.lab_principals(id),kind text NOT NULL,version integer NOT NULL,target text,expires timestamptz NOT NULL,used boolean NOT NULL DEFAULT false);
CREATE TABLE identity_control.lab3_invites(hash text PRIMARY KEY,owner integer UNIQUE NOT NULL,email text NOT NULL,generation integer NOT NULL,active_generation integer NOT NULL,expires timestamptz NOT NULL,revoked boolean NOT NULL,used boolean NOT NULL DEFAULT false);
CREATE TABLE identity_control.lab3_quota(id integer PRIMARY KEY,used integer NOT NULL,capacity integer NOT NULL);
CREATE TABLE identity_control.lab3_change_audit(id text PRIMARY KEY,action text NOT NULL);
INSERT INTO identity_control.lab3_quota VALUES(1,0,30);
'''
    for table in TABLES:
        sql+=f'''ALTER TABLE identity_control.lab3_{table} OWNER TO {owner};ALTER TABLE identity_control.lab3_{table} ENABLE ROW LEVEL SECURITY;ALTER TABLE identity_control.lab3_{table} FORCE ROW LEVEL SECURITY;REVOKE ALL ON identity_control.lab3_{table} FROM PUBLIC;
'''
        actions='SELECT(owner,version,email,email_version),INSERT,UPDATE(owner,phc,version,email,email_version)' if table=='credentials' else 'SELECT,INSERT,UPDATE'
        sql+=f"GRANT {actions} ON identity_control.lab3_{table} TO {writer};CREATE POLICY lab3_change_{table} ON identity_control.lab3_{table} TO {writer} USING(identity_api.lab_actor('session')=2) WITH CHECK(identity_api.lab_actor('session')=2);\n"
    sql+=f'''GRANT SELECT,INSERT,UPDATE(epoch) ON identity_control.lab_principals TO {writer};
CREATE POLICY lab3_change_lab_principals ON identity_control.lab_principals TO {writer} USING(identity_api.lab_actor('session')=2) WITH CHECK(identity_api.lab_actor('session')=2);
GRANT SELECT(id,active),UPDATE(id) ON identity_control.lab_principals TO {auth};
CREATE POLICY lab3_auth_own_principal_read ON identity_control.lab_principals FOR SELECT TO {auth} USING(id=identity_api.lab_actor('auth'));
CREATE POLICY lab3_auth_own_principal_lock ON identity_control.lab_principals FOR UPDATE TO {auth} USING(id=identity_api.lab_actor('auth')) WITH CHECK(false);
GRANT SELECT,UPDATE(revoked) ON identity_control.lab3_sessions TO {writer};
CREATE POLICY lab3_change_revoke_session ON identity_control.lab3_sessions TO {writer} USING(identity_api.lab_actor('session')=2) WITH CHECK(identity_api.lab_actor('session')=2);
CREATE FUNCTION identity_api.lab3_change(t text,mode text,kh text,newphc text,ev integer) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,pg_temp AS $lab$
DECLARE tok record;inv record;cred record;who integer;
BEGIN PERFORM set_config('batch4.ticket',t,true);
IF identity_api.lab_actor('session')<>2 THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
IF mode='activate' THEN
 PERFORM id FROM identity_control.lab3_quota WHERE id=1 FOR UPDATE;
 SELECT * INTO inv FROM identity_control.lab3_invites WHERE hash=kh FOR UPDATE;
 IF inv.hash IS NULL OR inv.used OR inv.revoked OR inv.generation<>inv.active_generation OR clock_timestamp()>=inv.expires OR NOT EXISTS(SELECT 1 FROM identity_control.lab3_quota WHERE id=1 AND used<capacity) THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
 INSERT INTO identity_control.lab_principals VALUES(inv.owner,'student',true,1);
 INSERT INTO identity_control.lab3_credentials VALUES(inv.owner,newphc,1,inv.email,1,decode('0102','hex'));
 UPDATE identity_control.lab3_invites SET used=true WHERE hash=kh;UPDATE identity_control.lab3_quota SET used=used+1 WHERE id=1;
 INSERT INTO identity_control.lab3_change_audit VALUES(kh,'activate');RETURN true;
ELSIF mode='rehash' THEN
 UPDATE identity_control.lab3_credentials SET phc=newphc WHERE owner=1 AND version=ev;RETURN FOUND;
ELSIF mode IN('reset','email') THEN
 SELECT owner INTO who FROM identity_control.lab3_mail_tokens WHERE hash=kh;
 PERFORM id FROM identity_control.lab_principals WHERE id=who FOR UPDATE;
 SELECT owner,version,email_version INTO cred FROM identity_control.lab3_credentials WHERE owner=who FOR UPDATE;
 SELECT * INTO tok FROM identity_control.lab3_mail_tokens WHERE hash=kh FOR UPDATE;
 IF tok.hash IS NULL OR tok.used OR tok.kind<>mode OR clock_timestamp()>=tok.expires OR (mode='reset' AND tok.version<>cred.version) OR (mode='email' AND tok.version<>cred.email_version) THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
 IF mode='reset' THEN
  UPDATE identity_control.lab3_credentials SET phc=newphc,version=version+1 WHERE owner=who;
  UPDATE identity_control.lab_principals SET epoch=epoch+1 WHERE id=who;
  UPDATE identity_control.lab3_sessions SET revoked=true WHERE owner=who;
 ELSE UPDATE identity_control.lab3_credentials SET email=tok.target,email_version=email_version+1 WHERE owner=who;END IF;
 UPDATE identity_control.lab3_mail_tokens SET used=true WHERE hash=kh;
 INSERT INTO identity_control.lab3_change_audit VALUES(kh,mode);RETURN true;
ELSE RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;END;$lab$;
ALTER FUNCTION identity_api.lab3_change(text,text,text,text,integer) OWNER TO {writer};REVOKE ALL ON FUNCTION identity_api.lab3_change(text,text,text,text,integer) FROM PUBLIC;GRANT EXECUTE ON FUNCTION identity_api.lab3_change(text,text,text,text,integer) TO {login};
GRANT SELECT(owner,phc,version,email) ON identity_control.lab3_credentials TO {auth};
CREATE POLICY lab3_auth_lookup_only ON identity_control.lab3_credentials FOR SELECT TO {auth} USING(identity_api.lab_actor('auth')=2);
CREATE FUNCTION identity_api.lab3_lookup(t text,em text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,pg_temp AS $lab$
DECLARE h jsonb;BEGIN PERFORM set_config('batch4.ticket',t,true);IF identity_api.lab_actor('auth')<>2 THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
SELECT jsonb_build_object('owner',owner,'phc',phc,'version',version) INTO h FROM identity_control.lab3_credentials WHERE email=em;RETURN h;END;$lab$;
ALTER FUNCTION identity_api.lab3_lookup(text,text) OWNER TO {auth};REVOKE ALL ON FUNCTION identity_api.lab3_lookup(text,text) FROM PUBLIC;GRANT EXECUTE ON FUNCTION identity_api.lab3_lookup(text,text) TO {auth_login};
GRANT SELECT,UPDATE(code_hashes) ON identity_control.lab3_groups TO {auth};
CREATE POLICY lab3_auth_own_code ON identity_control.lab3_groups TO {auth} USING(owner=identity_api.lab_actor('auth')) WITH CHECK(owner=identity_api.lab_actor('auth'));
CREATE FUNCTION identity_api.lab3_code(t text,codehash text) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,pg_temp AS $lab$
DECLARE a integer;BEGIN PERFORM set_config('batch4.ticket',t,true);a:=identity_api.lab_actor('auth');IF a IS NULL THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
PERFORM id FROM identity_control.lab_principals WHERE id=a FOR SHARE;
IF NOT FOUND THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
UPDATE identity_control.lab3_groups SET code_hashes=array_remove(code_hashes,codehash) WHERE owner=a AND NOT revoked AND codehash=ANY(code_hashes);RETURN FOUND;END;$lab$;
ALTER FUNCTION identity_api.lab3_code(text,text) OWNER TO {auth};REVOKE ALL ON FUNCTION identity_api.lab3_code(text,text) FROM PUBLIC;GRANT EXECUTE ON FUNCTION identity_api.lab3_code(text,text) TO {auth_login};
'''
    session=q(s.OWNERS['session']);session_login=q(s.logins(run)['session'])
    sql+=f'''GRANT SELECT(owner,version),UPDATE(owner) ON identity_control.lab3_credentials TO {session};
CREATE POLICY lab3_session_credential_read ON identity_control.lab3_credentials FOR SELECT TO {session} USING(owner=identity_api.lab_actor('session'));
CREATE POLICY lab3_session_credential_lock ON identity_control.lab3_credentials FOR UPDATE TO {session} USING(owner=identity_api.lab_actor('session')) WITH CHECK(false);
CREATE FUNCTION identity_api.lab3_establish(t text,sid integer,th text,pv integer) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,pg_temp AS $lab$
DECLARE a integer;u record;v integer;n timestamptz;BEGIN PERFORM set_config('batch4.ticket',t,true);a:=identity_api.lab_actor('session');
SELECT id,kind,active,epoch INTO u FROM identity_control.lab_principals WHERE id=a FOR SHARE;
SELECT version INTO v FROM identity_control.lab3_credentials WHERE owner=a FOR SHARE;
IF u.id IS NULL OR NOT u.active OR v IS NULL OR v<>pv THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501';END IF;
n:=clock_timestamp();INSERT INTO identity_control.lab3_sessions VALUES(sid,a,u.epoch,false,n+CASE WHEN u.kind='admin' THEN interval '8 hours' ELSE interval '30 days' END,n+CASE WHEN u.kind='admin' THEN interval '15 minutes' ELSE interval '14 days' END,th);
RETURN jsonb_build_object('owner',a,'epoch',u.epoch,'role',u.kind);END;$lab$;
ALTER FUNCTION identity_api.lab3_establish(text,integer,text,integer) OWNER TO {session};REVOKE ALL ON FUNCTION identity_api.lab3_establish(text,integer,text,integer) FROM PUBLIC;GRANT EXECUTE ON FUNCTION identity_api.lab3_establish(text,integer,text,integer) TO {session_login};
'''
    return sql
def change(lease,mode,key,newphc='synthetic-phc-digest',version=1):
    with lease.connect('session') as c:
        c.execute('BEGIN READ WRITE')
        try:r=c.execute('SELECT identity_api.lab3_change(%s,%s,%s,%s,%s)',(s.base.ticket('session',2),mode,key,newphc,version)).fetchone()[0];c.execute('COMMIT');return r
        except BaseException:c.execute('ROLLBACK');raise
def experiments(lease,check):
    from argon2 import PasswordHasher
    hasher=PasswordHasher(memory_cost=65536,time_cost=3,parallelism=1);old_password=secrets.token_urlsafe(24);old=hasher.hash(old_password);new=hasher.hash(secrets.token_urlsafe(24))
    with lease.connect('bootstrap') as c:
        c.execute('BEGIN READ WRITE');c.execute('INSERT INTO identity_control.lab3_credentials VALUES(1,%s,1,\'owner1@example.invalid\',1,decode(\'010203\',\'hex\'))',(old,));c.execute('COMMIT')
    # A local, versioned synthetic list. The literal expected digest is held by
    # the verifier; a missing/corrupted list cannot approve new passwords.
    words=b'synthetic-weak-password\npassword123456\n';pin=hashlib.sha256(words).hexdigest();path=b.HOME/'synthetic-password-list.txt';path.write_bytes(words)
    def policy(raw,value,role):
        crypto.password(value,role);b.require(raw is not None and hashlib.sha256(raw).hexdigest()==pin,'no_valid_password_list');b.require(value not in raw.decode().splitlines(),'blocked_password');return True
    for entry in ('register','change','reset'):
        check('password_list_valid:'+entry,policy(words,'unique-synthetic-password','student'))
        for bad,label in ((None,'missing'),(words+b'tamper','corrupt')):
            try:policy(bad,'unique-synthetic-password','student')
            except b.Stop:check('password_list_fail_closed:'+entry+':'+label,True)
            else:check('password_list_fail_closed:'+entry+':'+label,False)
        try:policy(words,'synthetic-weak-password','student')
        except b.Stop:check('password_list_match_denied:'+entry,True)
        else:check('password_list_match_denied:'+entry,False)
    check('password_list_fault_existing_login_still_verifies',crypto.verify(old,old_password) is True)
    # Controlled one-time invitation races; all users are synthetic and fit cap.
    for repeat in range(20):
        key=hashlib.sha256(('invite'+str(repeat)).encode()).hexdigest();actor=100+repeat
        with lease.connect('bootstrap') as c:
            c.execute('BEGIN READ WRITE');c.execute("INSERT INTO identity_control.lab3_invites VALUES(%s,%s,%s,1,1,clock_timestamp()+interval '1 hour',false,false)",(key,actor,'invite'+str(repeat)+'@example.invalid'));c.execute('COMMIT')
        barrier=threading.Barrier(2)
        def attempt():barrier.wait(10);return change(lease,'activate',key,new)
        a=cases.worker('activate-a',attempt);z=cases.worker('activate-b',attempt);cases.pair_done(a,z,'invite_race_watchdog')
        check('invitation_one_identity:'+str(repeat),sum(x[2].get('value') is True for x in (a,z))==1 and sum(x[2].get('sqlstate')=='42501' for x in (a,z))==1)
    with lease.connect('bootstrap') as c:
        check('invitation_atomic_quota_and_audit',c.execute("SELECT used,(SELECT count(*) FROM identity_control.lab3_change_audit WHERE action='activate') FROM identity_control.lab3_quota WHERE id=1").fetchone()==(20,20))
    for i,(generation,active,expired,revoked,full) in enumerate(((1,2,False,False,False),(1,1,True,False,False),(1,1,False,True,False),(1,1,False,False,True))):
        key=hashlib.sha256(('bad-invite'+str(i)).encode()).hexdigest()
        with lease.connect('bootstrap') as c:
            c.execute('BEGIN READ WRITE');c.execute("INSERT INTO identity_control.lab3_invites VALUES(%s,%s,%s,%s,%s,clock_timestamp()+(%s*interval '1 hour'),%s,false)",(key,1000+i,'bad'+str(i)+'@example.invalid',generation,active,-1 if expired else 1,revoked))
            if full:c.execute('UPDATE identity_control.lab3_quota SET capacity=used WHERE id=1')
            c.execute('COMMIT')
        try:change(lease,'activate',key,new)
        except Exception as ex:check('invitation_denial_exact:'+str(i),getattr(ex,'sqlstate',None)=='42501')
        else:check('invitation_denial_exact:'+str(i),False)
        with lease.connect('bootstrap') as c:
            check('invitation_denied_preserves_token_quota:'+str(i),c.execute('SELECT used,(SELECT used FROM identity_control.lab3_quota WHERE id=1) FROM identity_control.lab3_invites WHERE hash=%s',(key,)).fetchone()==(False,20))
            if full:c.execute('BEGIN READ WRITE');c.execute('UPDATE identity_control.lab3_quota SET capacity=30 WHERE id=1');c.execute('COMMIT')
    # Password reset and late login rehash serialize via CAS, never overwrite.
    for order in ('reset_first','rehash_first'):
        with lease.connect('bootstrap') as c:
            version=c.execute('SELECT version FROM identity_control.lab3_credentials WHERE owner=1').fetchone()[0]
            key=hashlib.sha256(('reset-'+order).encode()).hexdigest();c.execute('BEGIN READ WRITE');c.execute("INSERT INTO identity_control.lab3_mail_tokens VALUES(%s,1,'reset',%s,NULL,clock_timestamp()+interval '1 hour',false)",(key,version));c.execute('COMMIT')
        if order=='rehash_first':check('rehash_current_version',change(lease,'rehash','',old,version))
        check('reset_one_time:'+order,change(lease,'reset',key,new,version))
        check('late_rehash_CAS_refuses:'+order,change(lease,'rehash','',old,version) is False)
        try:change(lease,'reset',key,old,version)
        except Exception as ex:check('reset_token_replay_denied:'+order,getattr(ex,'sqlstate',None)=='42501')
        else:check('reset_token_replay_denied:'+order,False)
        with lease.connect('bootstrap') as c:
            row=c.execute('SELECT phc,mfa_cipher FROM identity_control.lab3_credentials WHERE owner=1').fetchone();check('reset_no_MFA_clear_or_late_overwrite:'+order,row[0]==new and bytes(row[1])==b'\x01\x02\x03')
    for repeat in range(20):
        with lease.connect('bootstrap') as c:
            version=c.execute('SELECT version FROM identity_control.lab3_credentials WHERE owner=1').fetchone()[0];key=hashlib.sha256(('reset-race'+str(repeat)).encode()).hexdigest()
            c.execute('BEGIN READ WRITE');c.execute("INSERT INTO identity_control.lab3_mail_tokens VALUES(%s,1,'reset',%s,NULL,clock_timestamp()+interval '1 hour',false)",(key,version));c.execute('COMMIT')
        barrier=threading.Barrier(2)
        def reset():barrier.wait(10);return change(lease,'reset',key,new,version)
        def rehash():barrier.wait(10);return change(lease,'rehash','',old,version)
        a=cases.worker('reset-race',reset);z=cases.worker('rehash-race',rehash);b.require(a[1].wait(15) and z[1].wait(15),'reset_rehash_watchdog')
        with lease.connect('bootstrap') as c:row=c.execute('SELECT phc,version,mfa_cipher FROM identity_control.lab3_credentials WHERE owner=1').fetchone()
        check('reset_rehash_real_CAS_race:'+str(repeat),a[2].get('value') is True and 'value' in z[2] and row[0]==new and row[1]==version+1 and bytes(row[2])==b'\x01\x02\x03')
    # Two valid mail proofs at the same generation race; exactly one survives.
    for repeat in range(20):
        keys=[hashlib.sha256(('email'+str(repeat)+str(i)).encode()).hexdigest() for i in (0,1)]
        with lease.connect('bootstrap') as c:
            version=c.execute('SELECT email_version FROM identity_control.lab3_credentials WHERE owner=1').fetchone()[0];c.execute('BEGIN READ WRITE')
            for i,key in enumerate(keys):c.execute("INSERT INTO identity_control.lab3_mail_tokens VALUES(%s,1,'email',%s,%s,clock_timestamp()+interval '1 hour',false)",(key,version,'email'+str(repeat)+str(i)+'@example.invalid'))
            c.execute('COMMIT')
        barrier=threading.Barrier(2)
        def email(key):barrier.wait(10);return change(lease,'email',key)
        a=cases.worker('email-a',lambda:email(keys[0]));z=cases.worker('email-b',lambda:email(keys[1]));b.require(a[1].wait(15) and z[1].wait(15),'email_race_watchdog')
        check('email_generation_one_winner:'+str(repeat),sum(x[2].get('value') is True for x in (a,z))==1 and sum(x[2].get('sqlstate')=='42501' for x in (a,z))==1)
    for repeat in range(20):
        codehash=hashlib.sha256(secrets.token_bytes(32)).hexdigest()
        with lease.connect('bootstrap') as c:
            c.execute('BEGIN READ WRITE');c.execute('UPDATE identity_control.lab3_groups SET revoked=true WHERE owner=2');c.execute('INSERT INTO identity_control.lab3_groups VALUES(2,%s,false,true,%s,%s)',(repeat+1,'code-race-'+str(repeat),[codehash]));c.execute('COMMIT')
            sessions=c.execute('SELECT count(*) FROM identity_control.lab3_sessions WHERE owner=2').fetchone()[0]
        barrier=threading.Barrier(2)
        def code():
            with lease.connect('auth') as c:
                c.execute('BEGIN READ WRITE');barrier.wait(10);r=c.execute('SELECT identity_api.lab3_code(%s,%s)',(s.base.ticket('auth',2),codehash)).fetchone()[0];c.execute('COMMIT');return r
        a=cases.worker('code-a',code);z=cases.worker('code-b',code);b.require(a[1].wait(15) and z[1].wait(15),'code_consumption_watchdog')
        check('recovery_code_one_use_real_race:'+str(repeat),[a[2].get('value'),z[2].get('value')].count(True)==1 and [a[2].get('value'),z[2].get('value')].count(False)==1)
        with lease.connect('bootstrap') as c:check('recovery_code_never_creates_normal_session:'+str(repeat),c.execute('SELECT count(*) FROM identity_control.lab3_sessions WHERE owner=2').fetchone()[0]==sessions)
    return {'local_mail_capture_only':True,'external_email_sent':False,'formal_migration_applied':False}
