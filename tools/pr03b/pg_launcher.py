"""Batch 2: initialize one owned PG cluster, attest read-only, then stop it.

No business migrations, user tables, authentication flows, arbitrary SQL/test
targets, service registration, resume or general database execution interface.
The sole catalog write is creation of this fresh run's one empty test database.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
import uuid

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import target_guard as guard
import run_tests as batch1
import native_process as native
import pg_probe

TOOLCHAIN_RELATIVE = 'work/pr03b/toolchains/pg17.11-win64-7409a40dc2bda548/runtime'
PROVENANCE_RELATIVE = 'outputs/pr03b/s3-batch2-20261107-01/TOOLCHAIN-PROVENANCE-02.json'
PROVENANCE_SHA256 = 'c343a4a013cb7475a36b7a5a8e61caf42720f975a141815af09ffccf2a5c0a54'
SOURCE_NAMES = ('native_process.py', 'pg_probe.py', 'pg_launcher.py', 'selftest_batch2.py')


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _plain(value):
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _plain(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)):
        return [_plain(v) for v in value]
    return value


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(_plain(value), ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')


def _source_hashes() -> dict[str,str]:
    names = [ROOT/'tools/pr03b'/n for n in (*SOURCE_NAMES,'target_guard.py','run_tests.py','selftest_runner.py')]
    names.append(ROOT/'web/backend/classroom_web/test_support.py')
    return {p.relative_to(ROOT).as_posix(): _sha(p.read_bytes()) for p in names}


def _toolchain(*, full: bool) -> tuple[dict,Path]:
    raw = guard._read_file(ROOT/PROVENANCE_RELATIVE, 4*1024*1024)
    if _sha(raw) != PROVENANCE_SHA256:
        guard._reject('toolchain provenance pin differs')
    manifest = guard.strict_json(raw)
    runtime = guard.contained_path(str(ROOT/TOOLCHAIN_RELATIVE), ROOT/'work/pr03b/toolchains')
    if not manifest.get('passed') or manifest.get('runtime_file_count') != 2446:
        guard._reject('toolchain payload manifest differs')
    if full:
        actual = set()
        for path in runtime.rglob('*'):
            guard.no_reparse(path)
            if path.is_file():
                actual.add(path.relative_to(runtime).as_posix())
        if actual != set(manifest['runtime_files']):
            guard._reject('toolchain file set differs')
        for name, info in manifest['runtime_files'].items():
            path = guard.contained_path(str(runtime/name), runtime)
            data = guard._read_file(path,64*1024*1024)
            if len(data)!=info['bytes'] or _sha(data)!=info['sha256']:
                guard._reject('toolchain payload hash differs')
    return manifest,runtime


def _tool(name: str) -> Path:
    if name not in {'postgres.exe','initdb.exe','pg_controldata.exe'}:
        guard._reject('helper is outside the fixed allowlist')
    manifest,runtime = _toolchain(full=False)
    info=manifest['executables'][name]
    path=guard.contained_path(str(runtime/info['relative_path']),runtime)
    if path != runtime/'Library/bin'/name or _sha(guard._read_file(path,64*1024*1024))!=info['sha256']:
        guard._reject('helper executable pin differs')
    return path


def _environment(work: Path) -> dict[str,str]:
    # Never hide an unsafe inherited database override by sanitizing it first.
    guard.check_environment(os.environ)
    env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','PROCESSOR_ARCHITECTURE'}}
    env.update(PATH=str((ROOT/TOOLCHAIN_RELATIVE)/'Library/bin')+os.pathsep+str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32'),
               TEMP=str(work),TMP=str(work),TMPDIR=str(work),LC_ALL='C',LANG='C',
               PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1',PYTHONUTF8='1')
    return env


def _helper(name: str, args: list[str], plan: guard.RunPlan, env: dict, commands: list, *, timeout: int=30) -> str:
    # Fixed callers below supply all arguments; no CLI argument forwarding.
    if name not in {'initdb.exe','pg_controldata.exe'}:
        guard._reject('helper is outside the catalog-bootstrap allowlist')
    exe=_tool(name)
    began=time.monotonic()
    row={'argv':[str(exe),*args],'cwd':str(plan.run_root),'exit_code':None,'job_cleanup_confirmed':False}
    commands.append(row)
    out=plan.run_root/f'helper-{len(commands)}-{name}.stdout.log'
    err=plan.run_root/f'helper-{len(commands)}-{name}.stderr.log'
    job=None
    try:
        job=native.NativeJob(plan)
        process=job.start_helper(name.removesuffix('.exe'),args,_sha(guard._read_file(exe,64*1024*1024)),env,out,err)
        row['created_process']=_plain(process)
        if not job.wait(timeout):
            row['timed_out']=True
            guard._reject('owned bootstrap helper timed out')
        row['exit_code']=job.exit_code()
    finally:
        try:
            if job is not None:
                try:
                    job.close()
                except BaseException as exc:
                    row['cleanup_error_type']=type(exc).__name__
                    raise
                finally:
                    row['job_cleanup_confirmed']=job.cleanup_confirmed is True
        finally:
            row['seconds']=round(time.monotonic()-began,3)
            for label,path in [('stdout',out),('stderr',err)]:
                try:
                    row[label]=guard._read_file(path,8*1024*1024).decode('utf-8','replace')
                except (guard.UnsafeTestDatabaseTarget,OSError):
                    row[label]='[owned diagnostic unavailable]'
    if row['exit_code']!=0 or not row['job_cleanup_confirmed']:
        guard._reject('fixed PostgreSQL helper failed')
    return row['stdout']


def _bounded(operation, job, *, seconds: float=6.0):
    """Abort the owned Job on client expiry; never reuse a timed-out worker."""
    done=threading.Event(); box={}
    def worker():
        try:
            box['result']=operation()
        except BaseException as exc:
            box['error_type']=type(exc).__name__
        finally:
            done.set()
    thread=threading.Thread(target=worker,daemon=True,name='pr03b-fixed-probe')
    thread.start()
    if not done.wait(seconds):
        job.close()
        done.wait(2)
        guard._reject('owned probe watchdog expired; run aborted')
    if 'error_type' in box:
        guard._reject('owned probe failed closed')
    return box['result']


def _configurations(plan: guard.RunPlan) -> dict[str,str]:
    def quoted(path):
        return str(path).replace('\\','/').replace("'","''")
    settings = f"""# PR03b Batch2 fresh synthetic cluster only
listen_addresses = '127.0.0.1'
port = {plan.port}
unix_socket_directories = ''
ssl = off
hba_file = '{quoted(plan.data_directory/'pg_hba.conf')}'
ident_file = '{quoted(plan.data_directory/'pg_ident.conf')}'
max_connections = 8
shared_buffers = '16MB'
autovacuum = off
max_parallel_workers = 0
max_parallel_workers_per_gather = 0
default_transaction_read_only = on
password_encryption = 'scram-sha-256'
shared_preload_libraries = ''
session_preload_libraries = ''
local_preload_libraries = ''
archive_mode = off
archive_command = ''
max_wal_senders = 0
max_replication_slots = 0
wal_level = minimal
logging_collector = off
log_destination = 'stderr'
log_statement = 'all'
log_connections = on
log_disconnections = on
log_line_prefix = '%m [%p] %u@%d '
"""
    hba = (f'host postgres,{plan.database} {plan.login} 127.0.0.1/32 scram-sha-256\n'
           'host all all 0.0.0.0/0 reject\nhost all all ::0/0 reject\nlocal all all reject\n')
    content={'postgresql.conf':settings,'pg_hba.conf':hba,'pg_ident.conf':'# No ident mappings\n',
             'postgresql.auto.conf':'# No ALTER SYSTEM settings\n'}
    pins={}
    for name,text in content.items():
        path=plan.data_directory/name
        guard.no_reparse(path)
        path.write_text(text,encoding='utf-8',newline='\n')
        pins[name]=_sha(path.read_bytes())
    return pins


class _LiveLease:
    """One in-memory, nontransferable owned run. Saved reports are never input."""
    def __init__(self, plan, job, process, pins, system_id):
        self.plan,self.job,self.process=plan,job,process
        self.pins,self.system_id=pins,system_id
        self.probe_count=0; self.events=[]; self.created=False

    def _local(self):
        for name,digest in self.pins.items():
            if _sha(guard._read_file(self.plan.data_directory/name,32768))!=digest:
                guard._reject('owned cluster configuration changed')
        if not self.job.contains(self.process.pid):
            guard._reject('postmaster is no longer in the owned Job')
        endpoint=native.verify_owned_endpoint(self.plan,self.process,self.job.parent,self.job)
        local=guard.verify_local_ownership(self.plan)
        network=native.job_network(self.job)
        if not network['loopback_only_observed'] or network['non_loopback_row_count']:
            guard._reject('owned Job has a non-loopback TCP observation')
        self.events.append({'phase':'fresh_local_boundary','endpoint':endpoint,'network':network})
        return local

    def _backend(self,pid):
        detail=native.observe_process_details(pid)
        if (detail.parent_pid!=self.process.pid or detail.identity.executable!=self.plan.postgres_executable
                or not self.job.contains(pid) or native.observe_process_details(pid)!=detail):
            guard._reject('SQL backend does not belong to the owned postmaster')
        self.events.append({'phase':'live_backend_link','backend_pid':pid,'parent_pid':detail.parent_pid,
                            'creation_token':detail.identity.creation_token,'job_membership':True})

    def _backend_finished(self, pid):
        deadline=time.monotonic()+2
        while self.job.contains(pid):
            if time.monotonic()>=deadline:
                guard._reject('closed probe backend did not exit')
            time.sleep(.01)

    def read(self, *, bootstrap=False):
        local=self._local()
        self.probe_count+=1
        result=_bounded(lambda:pg_probe.probe(self.plan,local,bootstrap=bootstrap,verify_backend=self._backend),self.job)
        self._backend_finished(result['backend_pid'])
        later=self._local()
        if later!=local or result['system_identifier']!=self.system_id:
            guard._reject('probe/local/control identity changed')
        if (result['config_file']!=str(self.plan.data_directory/'postgresql.conf')
                or result['hba_file']!=str(self.plan.data_directory/'pg_hba.conf')
                or result['ident_file']!=str(self.plan.data_directory/'pg_ident.conf')
                or result['unix_socket_directories']!=''
                or any(result[k] for k in ('shared_preload_libraries','session_preload_libraries','local_preload_libraries'))
                or result['archive_mode']!='off'):
            guard._reject('live server configuration differs')
        if not bootstrap and self.plan.database not in {d['name'] for d in result['catalog_snapshot']['databases']}:
            guard._reject('run database is absent')
        return result

    def create_empty_database(self):
        """Only this fresh run's single catalog DDL; never a business executor."""
        if self.created:
            guard._reject('test database creation is not repeatable')
        # Fresh native/local boundary immediately before the narrow exception.
        before=self._local()
        backend=[]
        def create():
            from psycopg import sql
            connection=None
            try:
                connection=pg_probe.psycopg.connect(host='127.0.0.1',hostaddr='127.0.0.1',port=self.plan.port,
                    user=self.plan.login,password=guard.SYNTHETIC_PASSWORD,dbname='postgres',
                    connect_timeout=2,sslmode='disable',gssencmode='disable',autocommit=True,
                    options='-c default_transaction_read_only=off -c statement_timeout=3000 -c lock_timeout=1000 -c search_path=pg_catalog')
                self._backend(connection.info.backend_pid)
                backend.append(connection.info.backend_pid)
                self._local()
                connection.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(self.plan.database)))
            finally:
                if connection is not None:
                    connection.close()
        _bounded(create,self.job)
        self._backend_finished(backend[0])
        after=self._local()
        if after!=before:
            guard._reject('database bootstrap boundary changed')
        self.created=True
        self.events.append({'phase':'single_catalog_ddl','action':'CREATE DATABASE','database':self.plan.database,
                            'template':'template0','application_tables_created':0,'repeat_allowed':False})


def _reserve_port():
    reservation=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    reservation.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
    reservation.bind(('127.0.0.1',0))
    port=reservation.getsockname()[1]
    if port==5432 or native.observe_listeners(port):
        reservation.close();guard._reject('selected private port is unavailable')
    return reservation,port


def _negative_boundaries(lease):
    """Real run marker corruption must refuse before any further connection."""
    path=lease.plan.run_root/'owner.json'
    raw=guard._read_file(path,32768); value=guard.strict_json(raw)
    value['instance_nonce']='0'*64 if value['instance_nonce']!='0'*64 else '1'*64
    count=lease.probe_count
    try:
        guard.no_reparse(path)
        _write(path,value)
        try:
            lease.read()
        except guard.UnsafeTestDatabaseTarget:
            pass
        else:
            guard._reject('mismatched owner marker was accepted')
        if lease.probe_count!=count:
            guard._reject('owner rejection occurred after connection admission')
    finally:
        guard.no_reparse(path)
        path.write_bytes(raw)
    return {'case':'real_marker_mismatch_before_connection','passed':True,'additional_probe_calls':0}


def live_verify(output: Path) -> int:
    guard.check_environment(os.environ)
    if os.name!='nt' or sys.version_info[:2]!=(3,12) or pg_probe.psycopg.__version__!='3.3.3':
        guard._reject('requires the pinned Windows/Python/psycopg platform')
    output=guard.contained_path(str(output),ROOT/'outputs/pr03b')
    if output.exists():guard._reject('output must be new')
    _toolchain(full=True)
    sources=_source_hashes()
    run_id=uuid.uuid4().hex
    run_root=guard.contained_path(str(ROOT/'work/pr03b/runs'/run_id),ROOT/'work/pr03b/runs')
    if run_root.exists():guard._reject('run directory already exists')
    reservation,port=_reserve_port()
    try:
        run_root.mkdir(parents=True,exist_ok=False);(run_root/'tmp').mkdir();(run_root/'pg').mkdir()
        output.mkdir(parents=True,exist_ok=False)
        manifest={'schema_version':1,'kind':'pr03b.synthetic_run_plan','run_id':run_id,'instance_nonce':uuid.uuid4().hex+uuid.uuid4().hex,
                  'worktree':str(ROOT),'data_directory':str(run_root/'pg/data'),'port':port,
                  'database':'pr02a_'+run_id,'login':'pr02a_'+run_id+'_bootstrap',
                  'postgres_executable':str(_tool('postgres.exe')),'postgres_sha256':_sha(_tool('postgres.exe').read_bytes()),
                  'server_version_num':170011}
        url=f"postgresql+psycopg://{manifest['login']}@127.0.0.1:{port}/{manifest['database']}"
        plan=guard.validate_plan(url,manifest,ROOT,os.environ)
        _write(run_root/'plan.json',manifest)
        env=_environment(run_root/'tmp')
    except BaseException:
        reservation.close()
        raise
    commands=[];job=None;lease=None;process=None
    report={'passed':False,'run_id':run_id,'work':str(run_root),'target_verified':False,'target_active':False,
            'database_execution_allowed':False,'business_migrations_executed':False,'business_data_modified':False,
            'source_sha256_before':sources,'toolchain_provenance_sha256':PROVENANCE_SHA256,'checks':[],'bootstrap_catalog_ddl':[]}
    phase='native_listener_control'
    try:
        # Only our exclusive reservation is used for this capability control.
        # Refuse a broken observer before initdb, helpers, PG or SQL can run.
        reservation.listen(1)
        report['native_listener_control']={'pid':os.getpid(),'address':'127.0.0.1','port':port,
            'socket_acceptconn':reservation.getsockopt(socket.SOL_SOCKET,socket.SO_ACCEPTCONN),
            'listener_only':_plain(native.observe_listeners(port)),
            'all_tcp_selected_owner':_plain(tuple(row for row in native.tcp_rows({os.getpid()}) if row.local_port==port))}
        if report['native_listener_control']['socket_acceptconn']!=1:
            guard._reject('own native listener control did not enter listening state')
        report['native_listener_control']['verified']=native.verify_listener_views(port,os.getpid())
        phase='initdb'
        pw=run_root/'synthetic.pw';pw.write_text(guard.SYNTHETIC_PASSWORD+'\n',encoding='ascii')
        _helper('initdb.exe',['-D',str(plan.data_directory),'-U',plan.login,'--pwfile',str(pw),
                            '--encoding=UTF8','--locale=C','--auth-local=reject','--auth-host=scram-sha-256','--no-instructions'],plan,env,commands)
        pw.unlink()
        pins=_configurations(plan)
        control=_helper('pg_controldata.exe',['-D',str(plan.data_directory)],plan,env,commands)
        match=re.search(r'^Database system identifier:\s*([0-9]+)\s*$',control,re.M)
        if not match:guard._reject('control-file system identifier unavailable')
        system_id=match.group(1)
        report.update(control_system_identifier=system_id,configuration_sha256=pins)
        phase='suspended_job_start'
        job=native.NativeJob(plan)
        # The exclusive reservation is released only at this final launch point.
        reservation.close();reservation=None
        process=job.start_postgres(env,run_root/'postgres.stdout.log',run_root/'postgres.stderr.log')
        deadline=time.monotonic()+20
        while True:
            if time.monotonic()>=deadline:guard._reject('owned PostgreSQL startup deadline expired')
            if not job.contains(process.pid):guard._reject('owned postmaster exited during startup')
            pid_file=plan.data_directory/'postmaster.pid'
            if pid_file.is_file():
                lines=guard._read_file(pid_file,8192).decode('utf-8').splitlines()
                if len(lines)>=8 and lines[7].strip()=='ready':break
            time.sleep(.1)
        if lines[0]!=str(process.pid):guard._reject('startup PID file differs from the created Job process')
        # Diagnostics are restricted to the process created by this Job and
        # this selected port. No foreign process command line is collected.
        if not job.contains(process.pid):guard._reject('startup process left the owned Job')
        observed=native.observe_process_details(process.pid)
        expected_args=native.fixed_postgres_arguments(plan)
        rows=native.observe_listeners(port)
        report['startup_native_diagnostic']={
            'created':_plain(process),'observed':_plain(observed),'expected_parent':_plain(job.parent),
            'expected_arguments':list(expected_args),'selected_port_listeners':_plain(rows),
            'equalities':{'identity':observed.identity==process,'parent':observed.parent_pid==job.parent.pid,
                          'arguments':observed.arguments==expected_args,'data_directory':observed.data_directory==plan.data_directory,
                          'listener':rows==(native.Listener('ipv4','127.0.0.1',port,process.pid),)}}
        native.verify_owned_endpoint(plan,process,job.parent,job)
        owner=manifest|{'kind':'pr03b.synthetic_cluster_owner','pid':process.pid,'creation_token':process.creation_token,
                       'postmaster_start_epoch':int(lines[2])}
        _write(run_root/'owner.json',owner)
        lease=_LiveLease(plan,job,process,pins,system_id)
        phase='bootstrap_read_only'
        bootstrap=lease.read(bootstrap=True)
        if plan.database in {d['name'] for d in bootstrap['catalog_snapshot']['databases']}:
            guard._reject('fresh cluster unexpectedly already has the run database')
        report['bootstrap_read_only']=bootstrap
        phase='create_empty_test_database'
        report['bootstrap_catalog_ddl']=[{'action':'CREATE DATABASE','database':plan.database,
                                        'application_tables':0,'result':'unknown_until_boundary_confirmed','attempts':1}]
        lease.create_empty_database()
        report['bootstrap_catalog_ddl'][0]['result']='confirmed'
        phase='final_read_only_identity'
        first=lease.read();report['final_read_only']=first
        report['checks'].append(_negative_boundaries(lease))
        second=lease.read()
        if first['catalog_sha256']!=second['catalog_sha256']:
            guard._reject('read-only verification changed the catalog snapshot')
        report['checks'].append({'case':'repeated_read_only_catalog_stable','passed':True})
        report['target_verified']=True
        report['verified_scope']='This fresh owned PG17.11 process/port/directory/system-id and exact synthetic DB/login during this run only'
        phase='owned_job_stop'
        lease._local()
        # Termination is solely by the private Job handle, never a PID file.
        # This one-shot verification does not promise a clean shutdown/reuse.
        job.close()
        report['stop_method']='private_Job_handle; one-shot data retained, never resumed'
        report['checks'].append({'case':'owned_postmaster_stopped','passed':True})
        try:
            lease._local()
        except guard.UnsafeTestDatabaseTarget:
            report['checks'].append({'case':'stale_stopped_run_rejected','passed':True})
        else:
            guard._reject('stopped run was accepted as current')
        report['passed']=True
    except Exception as exc:
        report.update(failed_phase=phase,error_type=type(exc).__name__)
        if isinstance(exc,guard.UnsafeTestDatabaseTarget):
            report['safe_error']=str(exc)
    finally:
        if reservation is not None:reservation.close()
        report['target_active']=None
        if job is not None:
            try:
                job.close()
                report['cleanup_job_closed']=job.cleanup_confirmed is True
                report['owned_listener_removed']=not any(r.pid==process.pid for r in native.observe_listeners(port)) if process else True
                if report['owned_listener_removed'] and report['cleanup_job_closed']:
                    report['target_active']=False
            except Exception as exc:
                report.update(passed=False,cleanup_error_type=type(exc).__name__)
        else:
            report['cleanup_job_closed']=True
            report['owned_listener_removed']=True
            report['target_active']=False
        report['helper_jobs_cleanup_confirmed']=all(c.get('job_cleanup_confirmed') is True for c in commands)
        if not report['helper_jobs_cleanup_confirmed']:
            report.update(passed=False,target_active=None)
        report.update(time_utc=datetime.now(timezone.utc).isoformat(),commands=commands,
                      events=lease.events if lease else [],source_sha256_after=_source_hashes())
        report['sources_unchanged']=report['source_sha256_before']==report['source_sha256_after']
        try:
            _toolchain(full=True)
            report['toolchain_verified_before_and_after']=True
        except Exception:
            report.update(passed=False,toolchain_verified_before_and_after=False)
        if not report['sources_unchanged'] or not report.get('cleanup_job_closed') or not report.get('owned_listener_removed'):
            report['passed']=False
        for filename in ('plan.json','owner.json','postgres.stdout.log','postgres.stderr.log'):
            path=run_root/filename
            try:
                raw=guard._read_file(path,8*1024*1024)
                (output/filename).write_bytes(raw)
            except (guard.UnsafeTestDatabaseTarget,OSError):
                report.setdefault('diagnostic_files_omitted',[]).append(filename)
        _write(output/'results.json',report)
    print(json.dumps({k:report[k] for k in ('passed','run_id','target_verified','target_active','database_execution_allowed')}))
    return 0 if report['passed'] else 1


def selftest(output: Path) -> int:
    guard.check_environment(os.environ)
    output=guard.contained_path(str(output),ROOT/'outputs/pr03b')
    if output.exists():guard._reject('output must be new')
    before=_source_hashes()
    work=ROOT/'work/pr03b/batch2-selftests'/uuid.uuid4().hex
    guard.no_reparse(work);work.mkdir(parents=True,exist_ok=False);output.mkdir(parents=True,exist_ok=False)
    batch1.install_selftest_io_guard()
    os.environ.update(TEMP=str(work),TMP=str(work),TMPDIR=str(work))
    import selftest_batch2
    report,log=selftest_batch2.run(work)
    report.update(source_sha256_before=before,source_sha256_after=_source_hashes(),target_verified=False,
                  database_execution_allowed=False,database_connections=0,evidence_level='batch2_synthetic_selftest',
                  executable=sys.executable,python=sys.version,time_utc=datetime.now(timezone.utc).isoformat())
    report['passed']=report['tests']>0 and not any(report[k] for k in ('failures','errors','skipped')) and before==report['source_sha256_after']
    (output/'selftest.log').write_text(log,encoding='utf-8',newline='\n');_write(output/'results.json',report)
    print(json.dumps({k:report[k] for k in ('passed','tests','failures','errors','skipped')}))
    return 0 if report['passed'] else 1


def main(argv=None) -> int:
    parser=batch1.SafeArgumentParser(prog='pr03b-pg-batch2',description=__doc__)
    sub=parser.add_subparsers(dest='mode',required=True,parser_class=batch1.SafeArgumentParser)
    for mode in ('selftest','verify-live'):
        item=sub.add_parser(mode);item.add_argument('--output-dir',required=True,type=Path)
    args=parser.parse_args(argv)
    try:
        guard.check_environment(os.environ)
        return selftest(args.output_dir) if args.mode=='selftest' else live_verify(args.output_dir)
    except Exception:
        parser.error('fixed synthetic PG verification failed closed')


if __name__=='__main__':
    raise SystemExit(main())
