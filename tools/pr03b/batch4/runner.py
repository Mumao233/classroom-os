"""Guest-only one-shot fixed ACL/RLS lab. No database/SQL/target CLI options."""
from __future__ import annotations
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from contextlib import contextmanager
from datetime import datetime,timezone
import json
import os
import re
import socket
import subprocess
import threading
import time
import uuid
import common as b
import schema as s
import cases

def role_identity_ok(row,plan,role):
    return (row is not None and len(row)==12 and row[:5]==(role,role,plan.database,'127.0.0.1',plan.port)
        and row[5]==plan.server_version_num and row[6:]==(False,False,False,False,False,False))

def snapshot_command_evidence(result,label):
    """Bounded diagnostics only. Admission still requires the full success gate."""
    out={'phase':label,'returncode':result.returncode,'stdout_bytes':len(result.stdout),
        'stderr_bytes':len(result.stderr),'stdout_sha256':b.sha(result.stdout),'stderr_sha256':b.sha(result.stderr)}
    if len(result.stdout)>1024*1024:return out
    try:
        v=b.strict(result.stdout)
        if type(v) is dict and v.get('passed') is False:
            stage=v.get('stage');failure=v.get('failure')
            if isinstance(stage,str) and re.fullmatch(r'[a-z0-9_]{1,64}',stage) and type(failure) is dict:
                safe={k:value for k,value in failure.items() if k in ('code','error_type') and
                    isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,180}',value)}
                out['failure']={'stage':stage,**safe}
    except (ValueError,UnicodeError,b.Stop):pass
    return out

def run_owned_helper(job,name,args,image_sha,env,run_root,read_file,receipt):
    """Capture the original Job HANDLE result before cleanup can replace it."""
    started=time.monotonic()
    receipt.update(helper=name,stage='starting',wait_completed=False,exit_code=None,
        exit_code_hex=None,exit_observed_before_cleanup=False,image_sha256=image_sha)
    try:
        identity=job.start_helper(name,args,image_sha,env,
            run_root/(name+'.stdout.log'),run_root/(name+'.stderr.log'))
        receipt['created_process']={'pid':identity.pid,'creation_token':identity.creation_token,'executable':str(identity.executable)}
        receipt['stage']='waiting'
        receipt['wait_completed']=job.wait(30) is True
        b.require(receipt['wait_completed'],'restricted_helper_deadline')
        receipt['stage']='reading_exit_code'
        code=job.exit_code()
        b.require(type(code) is int and 0<=code<=0xffffffff,'restricted_helper_exit_unavailable')
        receipt.update(exit_code=code,exit_code_hex=f'0x{code:08x}',exit_observed_before_cleanup=True,stage='exit_observed')
        b.require(code==0,'restricted_helper_exit:'+receipt['exit_code_hex'])
        receipt['stage']='reading_stdout'
        raw=read_file(run_root/(name+'.stdout.log'),1024*1024)
        receipt['stdout_bytes']=len(raw);receipt['stage']='complete'
        return raw.decode('utf-8','replace')
    finally:
        receipt['seconds']=round(time.monotonic()-started,3)

def configuration(plan):
    path=lambda p:str(p).replace('\\','/').replace("'","''")
    text=f"""# Batch4 fixed synthetic fixture only; default read only.
listen_addresses='127.0.0.1'
port={plan.port}
unix_socket_directories=''
ssl=off
hba_file='{path(plan.data_directory/'pg_hba.conf')}'
ident_file='{path(plan.data_directory/'pg_ident.conf')}'
max_connections=8
shared_buffers='16MB'
autovacuum=off
max_parallel_workers=0
max_parallel_workers_per_gather=0
default_transaction_read_only=on
password_encryption='scram-sha-256'
shared_preload_libraries=''
session_preload_libraries=''
local_preload_libraries=''
archive_mode=off
archive_command=''
max_wal_senders=0
max_replication_slots=0
wal_level=minimal
logging_collector=off
log_destination='stderr'
log_statement='none'
log_min_error_statement=panic
log_parameter_max_length=0
log_parameter_max_length_on_error=0
log_error_verbosity=terse
log_connections=off
log_disconnections=off
idle_in_transaction_session_timeout='10s'
"""
    hba=f'host postgres,{plan.database} {plan.login} 127.0.0.1/32 scram-sha-256\n'
    hba+=f'host {plan.database} '+','.join(s.logins(plan.run_id).values())+' 127.0.0.1/32 scram-sha-256\n'
    hba+='host all all 0.0.0.0/0 reject\nhost all all ::0/0 reject\nlocal all all reject\n'
    return {'postgresql.conf':text,'pg_hba.conf':hba,'pg_ident.conf':'# No mappings\n','postgresql.auto.conf':'# No overrides\n'}

class FixedConnection:
    """Internal wrapper rechecks the original native guard at transaction edges."""
    def __init__(self,c,lease):self.c,self.lease=c,lease
    def execute(self,statement,params=None,**kw):
        boundary=isinstance(statement,str) and statement.strip().upper() in ('BEGIN READ WRITE','BEGIN READ ONLY','COMMIT','ROLLBACK')
        if boundary:self.lease._local()
        result=self.c.execute(statement,params,**kw)
        if boundary:self.lease._local()
        return result

def fixed_run():
    guard,native,legacy,probe,wrapper,verified=b.bootstrap()
    import psycopg
    from psycopg.rows import dict_row
    from restricted_process import RestrictedJob
    report={'schema_version':1,'passed':False,'status':'STOP','batch':4,'evidence_level':'real_guest_fixed_sql',
        'database_execution_allowed':False,'target_verified':False,'target_active':None,
        'business_migrations_executed':False,'business_data_modified':False,'checks':[],
        'phases':[],'guard_snapshots':[],'snapshot_attempts':[],'bootstrap':verified,'time_utc':datetime.now(timezone.utc).isoformat()}
    evidence=b.HOME/'evidence'
    b.require(not evidence.exists() and not (b.HOME/'RUN.json').exists(),'one_shot_home_already_used')
    evidence.mkdir();run=uuid.uuid4().hex
    b.write(b.HOME/'RUN.json',{'run_id':run,'scope':'fixed Batch4 SQL only; never resume'})
    report['run_id']=run
    job=lease=process=reservation=None;commands=[];phase='preflight';helpers_ok=True
    source_digest=verified['bundle_sha256']
    def check(label,ok,detail=None):
        row={'case':label,'passed':bool(ok)}
        if detail is not None:row['evidence']=detail
        report['checks'].append(row)
        b.require(ok,'assertion_failed:'+label)
    def snapshot(label):
        b.require(b.bundle()==source_digest,'bundle_changed_at_boundary')
        b.original_inputs()
        system=wrapper._system_executable()
        b.require(system==verified['system'],'system_identity_changed')
        result=subprocess.run([wrapper.POWERSHELL_TEXT,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
            '-File',str(b.HOME/'coordinator.ps1')],capture_output=True,timeout=20,check=False)
        report['snapshot_attempts'].append(snapshot_command_evidence(result,label))
        b.require(result.returncode==0 and len(result.stdout)<=1024*1024 and not result.stderr,'os_snapshot_command_failed')
        data=b.strict(result.stdout)
        b.require(set(data)=={'passed','snapshot','pg_pids','system','maintenance'} and data['passed'] is True
            and wrapper.snapshot_topology_ok(data['snapshot']), 'os_isolation_changed')
        b.require(data['maintenance']['services_stopped'] is True and data['maintenance']['registry_matches'] is True
            and data['maintenance']['resume_pin']==b.RESUME_PIN,'maintenance_state_changed')
        expected=verified['system']['identity']
        b.require(all(data['system'][k]==expected[k] for k in expected if k!='time_utc'),'system_signature_changed')
        ids=data['pg_pids']
        b.require(type(ids) is list and all(type(v) is int and 0<v<=0xffffffff for v in ids) and len(set(ids))==len(ids),'pg_pid_shape')
        for pid in ids:
            b.require(job is not None and job.contains(pid),'foreign_postgres_refused')
        report['guard_snapshots'].append({'phase':label,**data})
    def phase_run(name,operation,seconds=45):
        nonlocal phase
        phase=name;snapshot(name+':before')
        # Original bounded executor kills this exact Job on expiry. The worker
        # is never reused; cleanup has its own bounded deadline.
        result=legacy._bounded(operation,job,seconds=seconds)
        snapshot(name+':after')
        report['phases'].append({'phase':name,'passed':True})
        return result
    def helper(name,args):
        nonlocal helpers_ok
        j=None;receipt={'helper':name,'stage':'creating_job'}
        try:
            j=RestrictedJob(plan)
            exe=legacy._tool(name+'.exe')
            return run_owned_helper(j,name,args,b.sha(guard._read_file(exe,64*1024*1024)),env,
                plan.run_root,guard._read_file,receipt)
        finally:
            if j is not None:
                try:j.close()
                finally:
                    helpers_ok=helpers_ok and j.cleanup_confirmed is True
                    receipt.update(cleanup_confirmed=j.cleanup_confirmed is True,restricted_token=j.token.record)
                    commands.append(receipt)
    class Lease(legacy._LiveLease):
        def _local(self):
            result=super()._local()
            b.require(b.bundle()==source_digest,'fixed_script_changed')
            b.require(self.job.token.process(self.process.pid)['administrative_groups_enabled'] is False,'postgres_admin_token')
            return result

        def _backend(self,pid):
            super()._backend(pid)
            token=self.job.token.process(pid)
            self.events.append({'phase':'actual_backend_restricted_token','backend_pid':pid,'token':token})

        @contextmanager
        def connect(self,service):
            b.require(service in ('bootstrap',*s.SERVICES,*s.DENIALS),'fixed_connection_role')
            self._local()
            role=self.plan.login if service=='bootstrap' else s.logins(run)[service]
            c=None;pid=None
            try:
                c=psycopg.connect(host='127.0.0.1',hostaddr='127.0.0.1',port=self.plan.port,dbname=self.plan.database,
                    user=role,password=guard.SYNTHETIC_PASSWORD if service=='bootstrap' else s.PASSWORD,
                    autocommit=True,connect_timeout=2,sslmode='disable',gssencmode='disable',
                    options='-c default_transaction_read_only=on -c statement_timeout=3000 -c lock_timeout=1000 -c search_path=pg_catalog')
                pid=c.info.backend_pid;self._backend(pid);self._local()
                if service=='bootstrap':
                    with c.cursor(row_factory=dict_row) as cur:
                        c.execute('BEGIN READ ONLY')
                        cur.execute(probe.IDENTITY_SQL);identity=probe._identity(self.plan,guard.verify_local_ownership(self.plan),cur.fetchone(),False)
                        b.require(identity['system_identifier']==self.system_id,'live_system_id_changed')
                        c.execute('ROLLBACK')
                else:
                    row=c.execute('''SELECT current_user::text,session_user::text,current_database(),
                        host(inet_server_addr()),inet_server_port(),current_setting('server_version_num')::integer,
                        rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolreplication,rolinherit
                        FROM pg_catalog.pg_roles WHERE rolname=current_user''').fetchone()
                    b.require(role_identity_ok(row,self.plan,role),'service_role_identity')
                yield FixedConnection(c,self)
                self._local()
            finally:
                if c is not None:c.close()
                if pid is not None and self.job._job:self._backend_finished(pid)
            self._local()
    try:
        snapshot('preflight:no_pg')
        legacy._toolchain(full=True)
        run_root=b.ROOT/'work/pr03b/runs'/run
        b.require(not run_root.exists(),'fresh_run_required')
        reservation,port=legacy._reserve_port()
        reservation.listen(1)
        report['listener_control']=native.verify_listener_views(port,os.getpid())
        run_root.mkdir(parents=True);(run_root/'tmp').mkdir();(run_root/'pg').mkdir()
        manifest={'schema_version':1,'kind':'pr03b.synthetic_run_plan','run_id':run,'instance_nonce':uuid.uuid4().hex+uuid.uuid4().hex,
            'worktree':str(b.ROOT),'data_directory':str(run_root/'pg/data'),'port':port,'database':'pr02a_'+run,
            'login':'pr02a_'+run+'_bootstrap','postgres_executable':str(legacy._tool('postgres.exe')),
            'postgres_sha256':b.sha(guard._read_file(legacy._tool('postgres.exe'),64*1024*1024)),'server_version_num':170011}
        plan=guard.validate_plan(f"postgresql+psycopg://{manifest['login']}@127.0.0.1:{port}/{manifest['database']}",manifest,b.ROOT,os.environ)
        b.write(run_root/'plan.json',manifest)
        env=legacy._environment(run_root/'tmp')
        phase='restricted_initdb'
        pw=run_root/'synthetic.pw';pw.write_text(guard.SYNTHETIC_PASSWORD+'\n',encoding='ascii')
        helper('initdb',['-D',str(plan.data_directory),'-U',plan.login,'--pwfile',str(pw),'--encoding=UTF8','--locale=C',
            '--auth-local=reject','--auth-host=scram-sha-256','--no-instructions'])
        pw.unlink()
        pins={}
        for name,text in configuration(plan).items():
            path=plan.data_directory/name;guard.no_reparse(path)
            path.write_text(text,encoding='utf-8',newline='\n');pins[name]=b.sha(guard._read_file(path,32768))
        control=helper('pg_controldata',['-D',str(plan.data_directory)])
        match=re.search(r'^Database system identifier:\s*([0-9]+)\s*$',control,re.M)
        b.require(match,'control_file_system_identifier');system_id=match.group(1)
        report.update(system_identifier=system_id,configuration_sha256=pins,plan=manifest)
        snapshot('before_postmaster:no_pg')
        phase='restricted_postmaster_start';job=RestrictedJob(plan)
        report['restricted_token']=job.token.record
        reservation.close();reservation=None
        process=job.start_postgres(env,run_root/'postgres.stdout.log',run_root/'postgres.stderr.log')
        report['actual_postmaster_token']=job.token.process(process.pid)
        deadline=time.monotonic()+20
        while True:
            b.require(time.monotonic()<deadline,'startup_deadline')
            b.require(job.contains(process.pid),'owned_postmaster_exited')
            pidfile=plan.data_directory/'postmaster.pid'
            if pidfile.is_file():
                lines=guard._read_file(pidfile,8192).decode('utf-8').splitlines()
                if len(lines)>=8 and lines[7].strip()=='ready':break
            time.sleep(.1)
        b.require(lines[0]==str(process.pid),'startup_pid_mismatch')
        native.verify_owned_endpoint(plan,process,job.parent,job)
        b.write(run_root/'owner.json',manifest|{'kind':'pr03b.synthetic_cluster_owner','pid':process.pid,
            'creation_token':process.creation_token,'postmaster_start_epoch':int(lines[2])})
        lease=Lease(plan,job,process,pins,system_id)
        report['initial_identity']=phase_run('read_only_fresh_cluster',lambda:lease.read(bootstrap=True))
        phase_run('single_empty_database',lease.create_empty_database)
        phase_run('read_only_fresh_database',lease.read)
        report['target_verified']=True
        report['fixed_script_lease']={'purpose':'Batch4 roles/ACL/RLS SQL only','active_during_fixed_phases':True,
            'general_database_execution':False,'run_id':run,'script_sha256':{n:b.sha(b.read(b.HOME/n)) for n in ('schema.py','cases.py','runner.py')}}
        phase_run('installation_atomicity',lambda:cases.installation_controls(lease.connect,run,check),seconds=90)
        def catalogs():
            with lease.connect('bootstrap') as c:
                c.execute('BEGIN READ ONLY');cases.catalog(c,run,check);c.execute('ROLLBACK')
        phase_run('catalog_role_acl',catalogs,seconds=90)
        for role in (*s.SERVICES,*s.DENIALS):
            phase_run('sql_role_'+role,lambda role=role:cases.role_cases(lease.connect,run,check,role),seconds=90)
        phase_run('sql_rls_fk_status',lambda:cases.structural_sql(lease.connect,run,check),seconds=90)
        phase_run('catalog_role_acl_final',catalogs,seconds=90)
        phase='final_live_identity'
        with lease.connect('bootstrap') as c:report['final_state_digest']=cases.digest(c)
        snapshot('final_live_guard')
        phase='owned_stop';lease._local();job.close()
        check('owned_job_stopped',job.cleanup_confirmed is True)
        try:lease._local()
        except guard.UnsafeTestDatabaseTarget:check('stale_run_guard_refuses',True)
        else:raise b.Stop('stale_run_accepted')
        report['passed']=True
    except BaseException as exc:
        report.update(passed=False,failed_phase=phase,error_type=type(exc).__name__)
        if isinstance(exc,b.Stop):report['safe_error']=str(exc)
        elif isinstance(exc,guard.UnsafeTestDatabaseTarget):report['safe_error']=str(exc)
        if getattr(exc,'sqlstate',None):report['sqlstate']=exc.sqlstate
    finally:
        if reservation is not None:reservation.close()
        report['target_active']=None
        try:
            if job is not None:job.close()
            report['job_cleanup_confirmed']=job is None or job.cleanup_confirmed is True
            report['listener_removed']=process is None or not any(x.pid==process.pid for x in native.observe_listeners(port))
            if report['job_cleanup_confirmed'] and report['listener_removed'] and helpers_ok:report['target_active']=False
        except BaseException as exc:report.update(passed=False,cleanup_error_type=type(exc).__name__)
        report['helpers']=commands
        report['fixed_script_lease_revoked']=True
        report['database_execution_allowed']=False
        try:
            # Closed job is deliberately not supplied as an active exception.
            job=None;snapshot('postflight:no_pg')
            legacy._toolchain(full=True)
            report['original_after']=b.load('_b4_after_verify',b.OLD/'approved_net_probe.py').verify_original()
            report['sources_and_old_inputs_unchanged']=True
        except BaseException as exc:report.update(passed=False,postflight_error_type=type(exc).__name__)
        if report['target_active'] is not False or not helpers_ok:report['passed']=False
        report['status']='PASS' if report['passed'] else 'STOP'
        report['checks_passed']=sum(x['passed'] for x in report['checks'])
        report['checks_total']=len(report['checks'])
        report['native_events']=lease.events if lease else []
        # Keep raw PG output/PGDATA locally. Only checksums are exported: error
        # diagnostics can otherwise include values from failed SQL constraints.
        report['diagnostic_hashes']={}
        if 'run_root' in locals():
            for p in run_root.glob('*.log'):
                report['diagnostic_hashes'][p.name]={'sha256':b.sha(guard._read_file(p,8*1024*1024)),'bytes':p.stat().st_size}
        b.write(evidence/'results.json',report)
    print(json.dumps({k:report[k] for k in ('status','passed','run_id','target_verified','target_active','database_execution_allowed','checks_total')}))
    return 0 if report['passed'] else 1

def main():
    if sys.argv[1:]!=['fixed-run']:
        print('STOP: only fixed-run is accepted');return 2
    try:return fixed_run()
    except BaseException as exc:
        # No trace/libpq text/SQL text/passwords in exported output.
        code=str(exc) if isinstance(exc,b.Stop) else getattr(exc,'code','')
        if not isinstance(code,str) or re.fullmatch(r'[A-Za-z0-9_:.-]{1,180}',code) is None:code='sanitized_exception'
        print(json.dumps({'status':'STOP','phase':'bootstrap_or_entry','error_type':type(exc).__name__,
            'code':code,'database_execution_allowed':False}));return 1

if __name__=='__main__':raise SystemExit(main())
