"""Fixed SQL cases. Not a user SQL executor; no CLI input is interpolated here."""
from __future__ import annotations
import hashlib
import schema as s
from common import Stop, require

def digest(c):
    value=c.execute('SELECT identity_api.lab_observe()').fetchone()[0]
    require(isinstance(value,str) and len(value)==32,'observer_shape')
    return value

def expect_error(c,statement,state,params=None):
    import psycopg
    before=digest(c)
    c.execute('SAVEPOINT denied')
    observed=None
    try:
        c.execute(statement,params)
    except psycopg.Error as exc:
        observed=exc.sqlstate
    finally:
        c.execute('ROLLBACK TO SAVEPOINT denied')
        c.execute('RELEASE SAVEPOINT denied')
    require(observed==state,'expected_sqlstate_'+state+'_observed_'+str(observed))
    require(digest(c)==before,'negative_changed_state')
    return {'expected_sqlstate':state,'observed_sqlstate':observed,'state_unchanged':True}

def catalog(c,run,check):
    roles=s.logins(run)
    owners=(*s.OWNERS.values(),s.TABLE_OWNER,s.CONTEXT_OWNER,s.OBSERVER)
    for role in (*roles.values(),*owners):
        row=c.execute('SELECT rolcanlogin,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolreplication,rolinherit FROM pg_catalog.pg_roles WHERE rolname=%s',(role,)).fetchone()
        check('flags:'+role,row==(role in roles.values(),False,False,False,False,False,False))
        count=c.execute('SELECT count(*) FROM pg_catalog.pg_auth_members WHERE member=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=%s) OR roleid=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=%s)',(role,role)).fetchone()[0]
        check('zero_membership:'+role,count==0)
        for schema in ('identity_control','identity_api','public'):
            check('schema_no_create:'+role+':'+schema,not c.execute('SELECT pg_catalog.has_schema_privilege(%s,%s,\'CREATE\')',(role,schema)).fetchone()[0] or role==s.TABLE_OWNER and schema!='public')
        if role in roles.values():
            check('no_raw_column_grants:'+role,c.execute("SELECT count(*) FROM information_schema.column_privileges WHERE grantee=%s AND table_schema='identity_control'",(role,)).fetchone()[0]==0)
            for table in s.TABLES:
                check('no_raw_table:'+role+':'+table,not c.execute("SELECT pg_catalog.has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')",(role,'identity_control.lab_'+table)).fetchone()[0])
            check('no_temp:'+role,not c.execute("SELECT pg_catalog.has_database_privilege(%s,current_database(),'TEMP')",(role,)).fetchone()[0])
            for service,signature in s.SIGNATURES.items():
                allowed=service=='observe' or roles.get(service)==role
                check('execute_matrix:'+role+':'+service,c.execute('SELECT pg_catalog.has_function_privilege(%s,%s,\'EXECUTE\')',(role,signature)).fetchone()[0] is allowed)
            check('no_context_helper:'+role,not c.execute("SELECT pg_catalog.has_function_privilege(%s,'identity_api.lab_actor(text)','EXECUTE')",(role,)).fetchone()[0])
    rows=c.execute("SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,r.rolname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace JOIN pg_catalog.pg_roles r ON r.oid=c.relowner WHERE n.nspname='identity_control' AND c.relkind='r' ORDER BY c.relname").fetchall()
    check('nine_force_rls_tables',len(rows)==9 and all(r[1:]==(True,True,s.TABLE_OWNER) for r in rows))
    functions=c.execute("SELECT p.proname,r.rolname,p.prosecdef,p.proconfig,p.prosrc FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace JOIN pg_catalog.pg_roles r ON r.oid=p.proowner WHERE n.nspname='identity_api' ORDER BY p.proname").fetchall()
    check('ten_fixed_definers',len(functions)==10)
    for name,owner,definer,config,body in functions:
        wanted=s.CONTEXT_OWNER if name=='lab_actor' else s.OBSERVER if name=='lab_observe' else s.OWNERS[name[4:]]
        check('definer:'+name,definer and owner==wanted and config==['search_path='+s.SEARCH_PATH])
        check('no_dynamic_sql:'+name,'EXECUTE' not in body.upper())
    public_exec=c.execute("SELECT count(*) FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a WHERE n.nspname='identity_api' AND a.grantee=0 AND a.privilege_type='EXECUTE'").fetchone()[0]
    check('no_public_execute',public_exec==0)
    # Exact column grants; superuser/table ownership do not mask this metadata.
    expected={
        ('status','cases'):{('SELECT','id'),('SELECT','executor'),('UPDATE','id')},
        ('status','operations'):{('SELECT',x) for x in ('id','case_id','executor','state')}|{('UPDATE','id')},
        ('auth','factors'):{('SELECT',x) for x in ('owner','id','ciphertext')},
        ('maintenance','guard'):{('SELECT','id'),('SELECT','version'),('UPDATE','version')},
        ('notice','notices'):{('SELECT',x) for x in ('id','attempts','delivered')}|{('UPDATE','attempts')}}
    for (service,table),wanted in expected.items():
        actual=set(c.execute('SELECT privilege_type,column_name FROM information_schema.column_privileges WHERE grantee=%s AND table_schema=\'identity_control\' AND table_name=%s',(s.OWNERS[service],'lab_'+table)).fetchall())
        check('column_acl:'+service+':'+table,actual==wanted)
    defaults=c.execute("SELECT r.rolname,d.defaclobjtype FROM pg_catalog.pg_default_acl d JOIN pg_catalog.pg_roles r ON r.oid=d.defaclrole WHERE d.defaclnamespace=0 AND d.defaclobjtype='f'").fetchall()
    check('default_execute_revoked',set(owners)|{'pr02a_'+run+'_bootstrap'} <= {r[0] for r in defaults})

def preinstall(c,run):
    names=(*s.logins(run).values(),*s.OWNERS.values(),s.TABLE_OWNER,s.CONTEXT_OWNER,s.OBSERVER)
    count=c.execute('SELECT count(*) FROM pg_catalog.pg_roles WHERE rolname=ANY(%s)',(list(names),)).fetchone()[0]
    require(count==0,'preexisting_lab_role_refused')
    require(c.execute("SELECT count(*) FROM pg_catalog.pg_namespace WHERE nspname IN ('identity_api','identity_control')").fetchone()[0]==0,'preexisting_lab_schema_refused')

def installation_controls(open_connection,run,check):
    # All connection creation comes from the owned-target lease in runner.py.
    with open_connection('bootstrap') as c:
        c.execute('BEGIN READ WRITE')
        c.execute(f'CREATE ROLE {s.ident(s.TABLE_OWNER)} LOGIN CREATEROLE')
        caught=False
        try:preinstall(c,run)
        except Stop as exc:caught=str(exc)=='preexisting_lab_role_refused'
        check('hostile_same_name_refused_before_install',caught)
        check('hostile_role_not_adopted',c.execute('SELECT rolcanlogin,rolcreaterole FROM pg_roles WHERE rolname=%s',(s.TABLE_OWNER,)).fetchone()==(True,True))
        c.execute('ROLLBACK')
        preinstall(c,run)
        check('hostile_control_rolled_back',True)
    import psycopg
    with open_connection('bootstrap') as c:
        c.execute('BEGIN READ WRITE');preinstall(c,run)
        c.execute(s.install(run),prepare=False)
        caught=False
        try:c.execute('SELECT 1/0')
        except psycopg.Error as exc:caught=exc.sqlstate=='22012'
        c.execute('ROLLBACK')
        require(caught,'late_install_failure_control')
        preinstall(c,run)
        check('full_install_late_failure_atomic_rollback',True)
    with open_connection('bootstrap') as c:
        c.execute('BEGIN READ WRITE');preinstall(c,run)
        c.execute(s.install(run),prepare=False)
        c.execute('COMMIT')
        check('lab_fixture_installed_once',True)

def role_cases(open_connection,run,check,only):
    roles=s.logins(run)
    require(only in (*s.SERVICES,*s.DENIALS),'fixed_role_case')
    for role in (only,):
        with open_connection(role) as c:
            c.execute('BEGIN READ WRITE')
            baseline=digest(c)
            for service in s.SERVICES:
                if service!=role:
                    evidence=expect_error(c,f'SELECT identity_api.lab_{service}(%s)','42501',(s.ticket(service),))
                    check('cross_execute:'+role+':'+service,True,evidence)
            for label,sql in (
                ('secret_select','SELECT ciphertext FROM identity_control.lab_factors'),
                ('private_select','SELECT body FROM identity_control.lab_documents'),
                ('factor_update',"UPDATE identity_control.lab_factors SET ciphertext='forged'"),
                ('epoch_update','UPDATE identity_control.lab_principals SET epoch=99'),
                ('authority_update','UPDATE identity_control.lab_guard SET version=99'),
                ('context_insert',"INSERT INTO identity_control.lab_contexts VALUES('forged',session_user,1,'runtime')"),
                ('schema_create','CREATE SCHEMA b4_attack'),
                ('role_create','CREATE ROLE b4_attack_role'),
                ('api_create','CREATE TABLE identity_api.b4_attack(id integer)'),
                ('control_create','CREATE TABLE identity_control.b4_attack(id integer)'),
                ('public_create','CREATE TABLE public.b4_attack(id integer)'),
                ('temp_create','CREATE TEMP TABLE b4_attack(id integer)'),
                ('function_create',"CREATE FUNCTION identity_api.b4_attack() RETURNS integer LANGUAGE sql AS 'SELECT 1'"),
                ('dynamic_sql',"DO $x$ BEGIN EXECUTE 'SELECT ciphertext FROM identity_control.lab_factors'; END $x$")):
                check(label+':'+role,True,expect_error(c,sql,'42501'))
            for owner in (*s.OWNERS.values(),s.TABLE_OWNER,s.CONTEXT_OWNER,s.OBSERVER):
                check('cannot_set_role:'+role+':'+owner,True,expect_error(c,'SET LOCAL ROLE '+s.ident(owner),'42501'))
            if role in s.SERVICES:
                for ticket_value in ('', 'forged', s.ticket('status' if role!='status' else 'runtime')):
                    c.execute("SELECT set_config('app.actor_id','1',true),set_config('app.owner_id','1',true),set_config('app.authority_id','5',true)")
                    check('forged_context:'+role+':'+ticket_value,True,expect_error(c,f'SELECT identity_api.lab_{role}(%s)','42501',(ticket_value,)))
                actor=5 if role in ('maintenance','notice','identity_cleanup') else 3 if role=='status' else 1
                result=c.execute(f'SELECT identity_api.lab_{role}(%s)',(s.ticket(role,actor),)).fetchone()[0]
                if role=='runtime':ok=result==[{'id':10,'body':'B4-PRIVATE-A'}]
                elif role=='auth':ok=result=={'has_factor':True}
                elif role=='status':ok=result==[{'op':100,'state':'reserved'}]
                else:ok=result=={'lab_effect':True}
                check('positive_entry:'+role,ok,{'result_projection_verified':ok})
                if role in ('runtime','auth','status'):
                    check('read_entry_zero_write:'+role,digest(c)==baseline)
                else:check('write_effect_observable:'+role,digest(c)!=baseline)
                if role=='runtime':
                    check('pool_context_switch_b',c.execute('SELECT identity_api.lab_runtime(%s)',(s.ticket(role,2),)).fetchone()[0]==[{'id':20,'body':'B4-PRIVATE-B'}])
                    check('admin_cannot_read_other_body',c.execute('SELECT identity_api.lab_runtime(%s)',(s.ticket(role,3),)).fetchone()[0] is None)
                    check('pool_empty_ticket_denied',True,expect_error(c,'SELECT identity_api.lab_runtime(%s)','42501',('',)))
                    c.execute('SET LOCAL search_path=pg_temp,public')
                    check('pinned_definer_search_path',c.execute('SELECT identity_api.lab_runtime(%s)',(s.ticket(role,1),)).fetchone()[0]==[{'id':10,'body':'B4-PRIVATE-A'}])
                if role=='status':
                    repeat=c.execute('SELECT identity_api.lab_status(%s)',(s.ticket(role,3),)).fetchone()[0]
                    check('status_repeat_zero_write',repeat==result and digest(c)==baseline)
                    other=c.execute('SELECT identity_api.lab_status(%s)',(s.ticket(role,4),)).fetchone()[0]
                    check('other_admin_only_own_op',other==[{'op':200,'state':'committed'}])
                    authority=c.execute('SELECT identity_api.lab_status(%s)',(s.ticket(role,5),)).fetchone()[0]
                    check('synthetic_authority_only_own_op',authority==[{'op':300,'state':'reserved'}])
            c.execute('ROLLBACK')
            c.execute('BEGIN READ WRITE')
            check('per_role_changes_rolled_back:'+role,digest(c)==baseline)
            c.execute('ROLLBACK')

def structural_sql(open_connection,run,check):
    with open_connection('bootstrap') as c:
        c.execute('BEGIN READ WRITE')
        check('composite_executor_fk',True,expect_error(c,"INSERT INTO identity_control.lab_operations VALUES(999,10,4,'reserved')",'23503'))
        check('composite_document_owner_fk',True,expect_error(c,"INSERT INTO identity_control.lab_documents VALUES(999,40,'synthetic')",'23503'))
        c.execute('SET LOCAL ROLE '+s.ident(s.TABLE_OWNER))
        check('table_owner_force_rls',c.execute('SELECT count(*) FROM identity_control.lab_documents').fetchone()[0]==0)
        c.execute('RESET ROLE')
        c.execute("SELECT set_config('batch4.ticket','B4-BOOTSTRAP-STATUS',true)")
        c.execute('SET LOCAL ROLE '+s.ident(s.OWNERS['status']))
        check('status_owner_positive_lock',c.execute('SELECT id FROM identity_control.lab_cases ORDER BY id FOR SHARE').fetchall()==[(10,)])
        # Observer isn't granted to this NOLOGIN owner; use a savepoint and a
        # bootstrap digest outside the temporarily selected owner instead.
        c.execute('SAVEPOINT lock_dml')
        observed=None
        import psycopg
        try:c.execute('UPDATE identity_control.lab_cases SET id=id WHERE id=10')
        except psycopg.Error as exc:observed=exc.sqlstate
        c.execute('ROLLBACK TO SAVEPOINT lock_dml');c.execute('RELEASE SAVEPOINT lock_dml')
        check('status_lock_privilege_not_dml',observed=='42501',{'observed_sqlstate':observed})
        c.execute('RESET ROLE')
        check('status_lock_control_no_write',c.execute('SELECT id,executor,target FROM identity_control.lab_cases WHERE id=10').fetchone()==(10,3,1))
        c.execute('ROLLBACK')
    # Full production qualification/reauth/cap state machine is outside this
    # fixture. Test only its underlying current-active and target-independence.
    for label,statement in (
        ('target_epoch_independent','UPDATE identity_control.lab_principals SET epoch=epoch+1 WHERE id=1'),
        ('executor_revocation','UPDATE identity_control.lab_principals SET active=false WHERE id=3')):
        with open_connection('bootstrap') as c:
            c.execute('BEGIN READ WRITE');c.execute(statement);c.execute('COMMIT')
        try:
            with open_connection('status') as c:
                c.execute('BEGIN READ WRITE')
                if label=='target_epoch_independent':
                    check(label,c.execute('SELECT identity_api.lab_status(%s)',(s.ticket('status',3),)).fetchone()[0]==[{'op':100,'state':'reserved'}])
                else:check(label,True,expect_error(c,'SELECT identity_api.lab_status(%s)','42501',(s.ticket('status',3),)))
                c.execute('ROLLBACK')
        finally:
            with open_connection('bootstrap') as c:
                c.execute('BEGIN READ WRITE')
                c.execute('UPDATE identity_control.lab_principals SET epoch=1,active=true WHERE id IN (1,3)')
                c.execute('COMMIT')
