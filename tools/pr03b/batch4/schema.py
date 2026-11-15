"""Fixed lab-only SQL fixture, not an Alembic/business migration or auth API.

Only run IDs and identifiers derived here vary. Synthetic contexts are seeded
by the trusted one-shot bootstrap, never created by a service LOGIN/GUC.
"""
from __future__ import annotations
import hashlib
import re

SERVICES = ('runtime','auth','session','recovery','status','maintenance','notice','identity_cleanup')
DENIALS = ('scheduler','dispatcher','local_reconciler','outsider')
OWNERS = dict(zip(SERVICES,('classroom_web_runtime','classroom_web_auth_verifier',
    'classroom_web_session_writer','classroom_web_manual_recovery_writer',
    'classroom_web_manual_status_reader','classroom_web_registration_maintainer',
    'classroom_web_identity_notice_worker','classroom_web_identity_history_cleaner')))
TABLE_OWNER='classroom_web_identity_table_owner'
CONTEXT_OWNER='pr03b_lab_context_reader'
OBSERVER='pr03b_lab_observer'
TABLES=('principals','contexts','documents','factors','guard','cases','operations','notices','effects')
SIGNATURES={s:f'identity_api.lab_{s}(text)' for s in SERVICES}
SIGNATURES['observe']='identity_api.lab_observe()'
SEARCH_PATH='pg_catalog, identity_control, public, pg_temp'
PASSWORD='pr03b-batch4-synthetic-only'

def quote(value):
    return "'"+str(value).replace("'","''")+"'"

def ident(value):
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}',value):
        raise ValueError('fixed identifier required')
    return '"'+value+'"'

def logins(run):
    if not isinstance(run,str) or not re.fullmatch(r'[0-9a-f]{32}',run):
        raise ValueError('exact new run ID required')
    return {s:'pr02a_'+run+'_'+s for s in (*SERVICES,*DENIALS)}

def ticket(service,actor=1):
    if service not in SERVICES or actor not in (1,2,3,4,5):
        raise ValueError('fixed synthetic context')
    return f'B4-SYNTHETIC-{service}-{actor}'

def function(name,returns,body,owner,args='t text'):
    return f'''CREATE FUNCTION identity_api.{name}({args}) RETURNS {returns}
LANGUAGE plpgsql SECURITY DEFINER SET search_path = {SEARCH_PATH} AS $lab$
{body}
$lab$;
ALTER FUNCTION identity_api.{name}({('' if not args else 'text')}) OWNER TO {ident(owner)};
REVOKE ALL ON FUNCTION identity_api.{name}({('' if not args else 'text')}) FROM PUBLIC;'''

def install(run):
    roles=logins(run); statements=[]
    owners=(*OWNERS.values(),TABLE_OWNER,CONTEXT_OWNER,OBSERVER)
    for role in (*owners,*roles.values()):
        login=role in roles.values()
        statements.append(f'CREATE ROLE {ident(role)} {"LOGIN" if login else "NOLOGIN"} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT'+
                          (f' PASSWORD {quote(PASSWORD)}' if login else '')+';')
    db='pr02a_'+run
    statements += [f'REVOKE ALL ON DATABASE {ident(db)} FROM PUBLIC;',
        f'GRANT CONNECT ON DATABASE {ident(db)} TO '+','.join(ident(v) for v in roles.values())+';',
        'REVOKE ALL ON SCHEMA public FROM PUBLIC;',
        f'CREATE SCHEMA identity_control AUTHORIZATION {ident(TABLE_OWNER)};',
        f'CREATE SCHEMA identity_api AUTHORIZATION {ident(TABLE_OWNER)};',
        'REVOKE ALL ON SCHEMA identity_control,identity_api FROM PUBLIC;']
    for owner in owners:
        statements += [f'GRANT USAGE ON SCHEMA identity_control,identity_api TO {ident(owner)};',
            f'ALTER DEFAULT PRIVILEGES FOR ROLE {ident(owner)} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;']
    statements += ['ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;',
        'GRANT USAGE ON SCHEMA identity_api TO '+','.join(ident(v) for v in roles.values())+';']
    statements.append('''
CREATE TABLE identity_control.lab_principals(id integer PRIMARY KEY, kind text NOT NULL, active boolean NOT NULL, epoch integer NOT NULL);
CREATE TABLE identity_control.lab_contexts(ticket_hash text PRIMARY KEY, login name NOT NULL, actor integer NOT NULL REFERENCES identity_control.lab_principals(id), purpose text NOT NULL);
CREATE TABLE identity_control.lab_documents(owner integer NOT NULL REFERENCES identity_control.lab_principals(id), id integer NOT NULL, body text NOT NULL, PRIMARY KEY(owner,id));
CREATE TABLE identity_control.lab_factors(owner integer NOT NULL REFERENCES identity_control.lab_principals(id), id integer NOT NULL, ciphertext text NOT NULL, PRIMARY KEY(owner,id));
CREATE TABLE identity_control.lab_guard(id integer PRIMARY KEY, version integer NOT NULL);
CREATE TABLE identity_control.lab_cases(id integer PRIMARY KEY, executor integer NOT NULL REFERENCES identity_control.lab_principals(id), target integer NOT NULL REFERENCES identity_control.lab_principals(id), UNIQUE(id,executor));
CREATE TABLE identity_control.lab_operations(id integer PRIMARY KEY, case_id integer NOT NULL, executor integer NOT NULL, state text NOT NULL CHECK(state IN ('reserved','committed')), FOREIGN KEY(case_id,executor) REFERENCES identity_control.lab_cases(id,executor));
CREATE TABLE identity_control.lab_notices(id integer PRIMARY KEY, attempts integer NOT NULL, delivered boolean NOT NULL);
CREATE TABLE identity_control.lab_effects(id integer PRIMARY KEY, actor integer NOT NULL REFERENCES identity_control.lab_principals(id), purpose text NOT NULL, eligible boolean NOT NULL);
INSERT INTO identity_control.lab_principals VALUES(1,'student',true,1),(2,'student',true,1),(3,'admin',true,1),(4,'admin',true,1),(5,'synthetic_authority',true,1);
INSERT INTO identity_control.lab_documents VALUES(1,10,'B4-PRIVATE-A'),(2,20,'B4-PRIVATE-B');
INSERT INTO identity_control.lab_factors VALUES(1,1,'B4-ENCRYPTED-SENTINEL');
INSERT INTO identity_control.lab_guard VALUES(1,1);
INSERT INTO identity_control.lab_cases VALUES(10,3,1),(20,4,2),(30,5,3);
INSERT INTO identity_control.lab_operations VALUES(100,10,3,'reserved'),(200,20,4,'committed'),(300,30,5,'reserved');
INSERT INTO identity_control.lab_notices VALUES(1,0,false);
INSERT INTO identity_control.lab_effects VALUES(90,1,'cleanup_fixture',true),(91,1,'keep_fixture',false);
''')
    for service in SERVICES:
        for actor in (1,2,3,4,5):
            statements.append('INSERT INTO identity_control.lab_contexts VALUES('+','.join((
                quote(hashlib.md5(ticket(service,actor).encode(),usedforsecurity=False).hexdigest()),quote(roles[service]),str(actor),quote(service)))+');')
    # Bootstrap-only instrumentation for testing the otherwise unreachable
    # status NOLOGIN owner's SELECT-lock / denied-UPDATE combination.
    statements.append('INSERT INTO identity_control.lab_contexts VALUES('+','.join((
        quote(hashlib.md5(b'B4-BOOTSTRAP-STATUS',usedforsecurity=False).hexdigest()),quote('pr02a_'+run+'_bootstrap'),'3',quote('status')))+');')
    for table in TABLES:
        statements += [f'ALTER TABLE identity_control.lab_{table} OWNER TO {ident(TABLE_OWNER)};',
            f'ALTER TABLE identity_control.lab_{table} ENABLE ROW LEVEL SECURITY;',
            f'ALTER TABLE identity_control.lab_{table} FORCE ROW LEVEL SECURITY;',
            f'REVOKE ALL ON identity_control.lab_{table} FROM PUBLIC;']
    # Test tickets model a broker projection, not production signatures/MFA.
    statements += [f'GRANT SELECT(ticket_hash,login,actor,purpose) ON identity_control.lab_contexts TO {ident(CONTEXT_OWNER)};',
        f'GRANT SELECT(id,active,kind) ON identity_control.lab_principals TO {ident(CONTEXT_OWNER)};',
        f'CREATE POLICY context_reader ON identity_control.lab_contexts FOR SELECT TO {ident(CONTEXT_OWNER)} USING(true);',
        f'CREATE POLICY context_principal ON identity_control.lab_principals FOR SELECT TO {ident(CONTEXT_OWNER)} USING(true);',
        f'''CREATE FUNCTION identity_api.lab_actor(p text) RETURNS integer LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = {SEARCH_PATH} AS $lab$
SELECT c.actor FROM identity_control.lab_contexts c JOIN identity_control.lab_principals u ON u.id=c.actor
WHERE c.ticket_hash=pg_catalog.md5(pg_catalog.current_setting('batch4.ticket',true))
AND c.login=SESSION_USER AND c.purpose=p AND u.active
$lab$;
ALTER FUNCTION identity_api.lab_actor(text) OWNER TO {ident(CONTEXT_OWNER)};
REVOKE ALL ON FUNCTION identity_api.lab_actor(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION identity_api.lab_actor(text) TO {','.join(ident(v) for v in OWNERS.values())};''']
    # Each definer owner has explicit action/column grants plus a narrow policy.
    grants={
      'runtime':[('documents','SELECT(owner,id,body)',"owner=identity_api.lab_actor('runtime')")],
      'auth':[('factors','SELECT(owner,id,ciphertext)',"owner=identity_api.lab_actor('auth')")],
      'session':[('effects','INSERT(id,actor,purpose,eligible)',"actor=identity_api.lab_actor('session') AND purpose='session'")],
      'recovery':[('effects','INSERT(id,actor,purpose,eligible)',"actor=identity_api.lab_actor('recovery') AND purpose='recovery'")],
      'maintenance':[('guard','SELECT(id,version)','identity_api.lab_actor(\'maintenance\')=5'),('guard','UPDATE(version)','identity_api.lab_actor(\'maintenance\')=5')],
      'notice':[('notices','SELECT(id,attempts,delivered)',"identity_api.lab_actor('notice')=5"),('notices','UPDATE(attempts)',"identity_api.lab_actor('notice')=5")],
      'identity_cleanup':[('effects','SELECT(id,eligible)',"eligible AND identity_api.lab_actor('identity_cleanup')=5"),('effects','DELETE',"eligible AND identity_api.lab_actor('identity_cleanup')=5")],
      'status':[('cases','SELECT(id,executor)',"executor=identity_api.lab_actor('status')"),('cases','UPDATE(id)',"executor=identity_api.lab_actor('status')"),
                ('operations','SELECT(id,case_id,executor,state)',"executor=identity_api.lab_actor('status')"),('operations','UPDATE(id)',"executor=identity_api.lab_actor('status')")]
    }
    for service,items in grants.items():
        for idx,(table,grant,predicate) in enumerate(items):
            action=grant.split('(')[0]
            statements.append(f'GRANT {grant} ON identity_control.lab_{table} TO {ident(OWNERS[service])};')
            using='' if action=='INSERT' else f'USING ({predicate})'
            check=f'WITH CHECK ({"false" if service=="status" else predicate})' if action in ('INSERT','UPDATE') else ''
            statements.append(f'CREATE POLICY p_{service}_{idx} ON identity_control.lab_{table} FOR {action} TO {ident(OWNERS[service])} {using} {check};')
    bodies={
      'runtime':"RETURN (SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('id',d.id,'body',d.body) ORDER BY d.id) FROM identity_control.lab_documents d);",
      'auth':"RETURN pg_catalog.jsonb_build_object('has_factor',EXISTS(SELECT 1 FROM identity_control.lab_factors f WHERE f.ciphertext IS NOT NULL));",
      'session':"INSERT INTO identity_control.lab_effects(id,actor,purpose,eligible) VALUES(101,a,'session',false); RETURN pg_catalog.jsonb_build_object('lab_effect',true);",
      'recovery':"INSERT INTO identity_control.lab_effects(id,actor,purpose,eligible) VALUES(102,a,'recovery',false); RETURN pg_catalog.jsonb_build_object('lab_effect',true);",
      'maintenance':"IF a<>5 THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501'; END IF; UPDATE identity_control.lab_guard SET version=version+1 WHERE id=1; RETURN pg_catalog.jsonb_build_object('lab_effect',FOUND);",
      'notice':"IF a<>5 THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501'; END IF; UPDATE identity_control.lab_notices SET attempts=attempts+1 WHERE id=1; RETURN pg_catalog.jsonb_build_object('lab_effect',FOUND);",
      'identity_cleanup':"IF a<>5 THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501'; END IF; DELETE FROM identity_control.lab_effects WHERE id=90 AND eligible; RETURN pg_catalog.jsonb_build_object('lab_effect',FOUND);",
      'status':"""PERFORM c.id FROM identity_control.lab_cases c WHERE c.executor=a ORDER BY c.id FOR SHARE;
PERFORM o.id FROM identity_control.lab_operations o WHERE o.executor=a ORDER BY o.id FOR SHARE;
RETURN (SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('op',o.id,'state',o.state) ORDER BY o.id) FROM identity_control.lab_operations o WHERE o.executor=a);"""
    }
    for service in SERVICES:
        body=f"""DECLARE a integer;
BEGIN
PERFORM pg_catalog.set_config('batch4.ticket',t,true);
a:=identity_api.lab_actor({quote(service)});
IF a IS NULL THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501'; END IF;
{bodies[service]}
END;"""
        statements.append(function('lab_'+service,'jsonb',body,OWNERS[service]))
        statements.append(f'GRANT EXECUTE ON FUNCTION {SIGNATURES[service]} TO {ident(roles[service])};')
    # Necessary fixed assertion hook: no body/ciphertext/ticket/key/target data.
    columns={'principals':'id,kind,active,epoch','contexts':'login,actor,purpose',
        'documents':'owner,id','factors':'owner,id','guard':'id,version','cases':'id,executor,target',
        'operations':'id,case_id,executor,state','notices':'id,attempts,delivered','effects':'id,actor,purpose,eligible'}
    pieces=[]
    for table,cols in columns.items():
        statements += [f'GRANT SELECT({cols}) ON identity_control.lab_{table} TO {ident(OBSERVER)};',
            f'CREATE POLICY observer ON identity_control.lab_{table} FOR SELECT TO {ident(OBSERVER)} USING(true);']
        pieces.append(f"SELECT {quote(table)} AS name,coalesce(pg_catalog.jsonb_agg(pg_catalog.to_jsonb(v) ORDER BY pg_catalog.to_jsonb(v)::text),'[]'::jsonb) AS value FROM (SELECT {cols} FROM identity_control.lab_{table}) v")
    statements.append(function('lab_observe','text',"BEGIN RETURN (SELECT pg_catalog.md5(pg_catalog.jsonb_object_agg(name,value)::text) FROM ("+' UNION ALL '.join(pieces)+") d); END;",OBSERVER,args=''))
    statements.append(f'GRANT EXECUTE ON FUNCTION identity_api.lab_observe() TO '+','.join(ident(v) for v in roles.values())+';')
    return '\n'.join(statements)+'\n'
