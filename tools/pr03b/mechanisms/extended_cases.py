"""Additional fixed ACL/lock counterexamples; never a SQL CLI."""
import threading,time
import common as b,schema as s,cases,identity_lab
FUNCTIONS={
 'lab3_write':('recovery','text,integer,text,text'),'lab3_status':('status','text,integer,integer,timestamptz'),
 'lab3_step':('session','text,bigint'),'lab3_notice':('notice','text,integer,text,integer'),
 'lab3_session':('session','text,text,integer,text,text'),'lab3_ticket':('session','text,text,text,integer,integer'),
 'lab3_web_proof':('session','text,text,text,integer,timestamptz'),'lab3_codes':('session','text,text,text,text,text[]'),
 'lab3_hash_admission':('auth','text,text,bigint,integer,text,timestamptz'),'lab3_fail_budget':('auth','text,text,integer,integer,boolean'),
 'lab3_registration':('maintenance','text,integer,text,integer,text,text,boolean'),
 'lab3_mfa_step':('session','text,bigint,timestamptz'),
 'lab3_login_admit':('auth','text,text,text,text,bigint,integer,text,timestamptz'),
 'lab3_login_finish':('auth','text,text,text,bigint,integer,text,boolean'),
 'lab3_preauth':('auth','text,text'),
 'lab3_change':('session','text,text,text,text,integer'),'lab3_lookup':('auth','text,text'),
 'lab3_code':('auth','text,text'),'lab3_establish':('session','text,integer,text,integer')}
def acl(lease,check):
    tables=(*s.TABLES,*identity_lab.TABLES)
    for service in (*s.SERVICES,*s.DENIALS):
        with lease.connect(service) as c:
            c.execute('BEGIN READ WRITE')
            for table in tables:
                cases.denied(c,'SELECT * FROM identity_control.lab3_'+table,(),check,'extension_direct_table_denied:'+service+':'+table)
            for name,(owner,types) in FUNCTIONS.items():
                signature='identity_api.'+name+'('+types+')'
                actual=c.execute("SELECT has_function_privilege(%s,'EXECUTE')",(signature,)).fetchone()[0]
                check('extension_function_execute_matrix:'+service+':'+name,actual is (owner==service))
            if service!='session':cases.denied(c,'SELECT identity_api.lab3_codes(NULL::text,NULL::text,NULL::text,NULL::text,NULL::text[])',(),check,'N02_non_session_role_cannot_receive_codes:'+service)
            c.execute('ROLLBACK')
    with lease.connect('bootstrap') as c:
        rows=c.execute("SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='identity_control' AND c.relname=ANY(%s)",(['lab3_'+x for x in tables],)).fetchall()
        check('extension_all_tables_FORCE_RLS_owner',len(rows)==len(tables) and all(x[1] and x[2] and x[3]==s.base.TABLE_OWNER for x in rows))
        funcs=c.execute("SELECT p.proname,p.prosecdef,p.proconfig,pg_get_userbyid(p.proowner),EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee=0 AND a.privilege_type='EXECUTE') FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='identity_api' AND p.proname LIKE 'lab3_%'").fetchall()
        check('extension_definers_no_PUBLIC_execute',set(x[0] for x in funcs)==set(FUNCTIONS) and all(x[1] and x[2] and x[3]==s.OWNERS[FUNCTIONS[x[0]][0]] and not x[4] for x in funcs))
def notices(lease,check):
    # An event-only transaction must complete while another connection holds
    # user UPDATE. Delivery takes a fresh user lock only AFTER event commit.
    for mode in ('claim','retry','ack','cleanup','delivery'):
        for order in ('user_first','event_first'):
            for repeat in range(20):
                nid=300000+1000*('claim','retry','ack','cleanup','delivery').index(mode)+100*('user_first','event_first').index(order)+repeat
                with lease.connect('bootstrap') as c:
                    c.execute('BEGIN READ WRITE')
                    c.execute("INSERT INTO identity_control.lab3_cases(id,executor,target,target_epoch,state,flow,receiver) SELECT %s,3,1,epoch,'open',1,'notice-only-control' FROM identity_control.lab_principals WHERE id=1",(nid,))
                    c.execute("INSERT INTO identity_control.lab3_ops VALUES(%s,%s,3,'issue','reserved',NULL)",(nid,nid))
                    c.execute('INSERT INTO identity_control.lab3_notices VALUES(%s,1,%s,0,0,false)',(nid,nid))
                    if mode in ('retry','ack','cleanup','delivery'):c.execute('UPDATE identity_control.lab3_notices SET lease=7,delivered=%s WHERE id=%s',(mode=='cleanup',nid))
                    if mode=='cleanup':c.execute('UPDATE identity_control.lab3_notices SET lease=0 WHERE id=%s',(nid,))
                    c.execute('COMMIT')
                with lease.connect('bootstrap') as user,lease.connect('notice') as event:
                    user.execute('BEGIN READ WRITE');event.execute('BEGIN READ WRITE')
                    user.execute("SET LOCAL lock_timeout='5s';SET LOCAL statement_timeout='10s'");event.execute("SET LOCAL lock_timeout='5s';SET LOCAL statement_timeout='10s'")
                    def lock_user():user.execute('SELECT id FROM identity_control.lab_principals WHERE id=1 FOR UPDATE')
                    def event_action():
                        action='ack' if mode=='delivery' else mode
                        return event.execute('SELECT identity_api.lab3_notice(%s,%s,%s,7)',(s.base.ticket('notice',5),nid,action)).fetchone()[0]
                    start=time.monotonic()
                    if order=='user_first':lock_user();ok=event_action()
                    else:ok=event_action();lock_user()
                    event.execute('COMMIT')
                    check('N01_event_no_reverse_user_lock:'+mode+':'+order+':'+str(repeat),ok is True and time.monotonic()-start<10)
                    user.execute('ROLLBACK')
                with lease.connect('bootstrap') as c:
                    c.execute('BEGIN READ WRITE');c.execute('DELETE FROM identity_control.lab3_notices WHERE id=%s',(nid,));c.execute('COMMIT')
    # Deliberately unsafe historical order is a positive detector control,
    # reported separately from the required zero-deadlock replacement paths.
    for repeat in range(20):
        with lease.connect('bootstrap') as sec,lease.connect('bootstrap') as obs:
            sec.execute('BEGIN READ WRITE');sec.execute("SET LOCAL deadlock_timeout='100ms';SET LOCAL lock_timeout='5s';SET LOCAL statement_timeout='10s'")
            sec.execute('SELECT id FROM identity_control.lab_principals WHERE id=1 FOR UPDATE')
            ready=threading.Event();pids=[]
            def old_local():
                with lease.connect('bootstrap') as c:
                    c.execute('BEGIN READ WRITE');c.execute("SET LOCAL deadlock_timeout='100ms';SET LOCAL lock_timeout='5s';SET LOCAL statement_timeout='10s'")
                    c.execute('SELECT owner FROM identity_control.lab3_business_counter WHERE owner=1 FOR UPDATE');pids.append(c.c.info.backend_pid);ready.set()
                    try:c.execute('INSERT INTO identity_control.lab3_business_outbox VALUES(%s,1,\'unsafe_control\')',(400000+repeat,));c.execute('ROLLBACK');return 'no_deadlock'
                    except BaseException:c.execute('ROLLBACK');raise
            thread,done,box=cases.worker('expected-old-lock-cycle',old_local)
            b.require(ready.wait(10) and cases.wait_blocked(obs,pids[0]),'old_control_interleave')
            code=None
            try:sec.execute('SELECT owner FROM identity_control.lab3_business_counter WHERE owner=1 FOR UPDATE')
            except BaseException as ex:code=getattr(ex,'sqlstate',None)
            sec.execute('ROLLBACK');b.require(done.wait(12),'old_control_watchdog')
            check('N01_expected_unsafe_control_deadlock:'+str(repeat),'40P01' in (code,box.get('sqlstate')),{'control_only':True,'sqlstates':[code,box.get('sqlstate')]})
