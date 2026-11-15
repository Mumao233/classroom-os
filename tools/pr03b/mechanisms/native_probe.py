"""Fixed prerequisite: same native guard/Job, fresh run, zero PG/SQL."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import common as b
def main():
    import os,socket,uuid,json
    b.require(sys.argv[1:]==[],'native_probe_no_arguments')
    guard,native,legacy,probe,wrapper,verified=b.bootstrap();sys.path[:0]=[str(b.BASE),str(b.ROOT/'tools/pr03b')]
    import windows
    windows.protect_process()
    run=uuid.uuid4().hex;root=b.ROOT/'work/pr03b/runs'/run;root.mkdir();(root/'tmp').mkdir();(root/'pg').mkdir()
    reservation,port=legacy._reserve_port();reservation.listen(1);native.verify_listener_views(port,os.getpid());reservation.close()
    manifest={'schema_version':1,'kind':'pr03b.synthetic_run_plan','run_id':run,'instance_nonce':uuid.uuid4().hex+uuid.uuid4().hex,'worktree':str(b.ROOT),'data_directory':str(root/'pg/data'),
        'port':port,'database':'pr02a_'+run,'login':'pr02a_'+run+'_bootstrap','postgres_executable':str(legacy._tool('postgres.exe')),'postgres_sha256':b.sha(b.read(legacy._tool('postgres.exe'),64*1024*1024)),'server_version_num':170011}
    plan=guard.validate_plan(f"postgresql+psycopg://{manifest['login']}@127.0.0.1:{port}/{manifest['database']}",manifest,b.ROOT,os.environ)
    rows=[]
    def check(name,ok,detail=None):
        rows.append({'case':name,'passed':bool(ok),'detail':detail});b.require(ok,'native_probe_assertion:'+name)
    result={'passed':False,'kind':'native_IPC_prerequisite','run_id':run,'run_root':str(root),'checks':rows,'postgres_started':False,'database_connections':0,'database_execution_allowed':False,'source_pin':verified['source_pin']}
    try:
        import time
        started=time.perf_counter()
        for _ in range(20):check('pinned_source_full_content_hash',b.bundle()==verified['bundle_sha256'])
        result['source_hash_20_checks_seconds']=round(time.perf_counter()-started,6)
        mini=None
        try:
            mini,identity=windows.start_python(plan,b.HOME/'native_minimal.py',[],legacy._environment(root/'tmp'),'native-minimal')
            completed=mini.wait(30);check('stdlib_minimal_child_completes',completed,{'exit_code':mini.exit_code()})
            check('stdlib_minimal_child_exit_zero',mini.exit_code()==0)
            result['kernel_exit_vs_membership']={'fixed_child_PID':identity.pid,'native_primary_handle_signaled':completed,'native_exit_code':mini.exit_code(),'IsProcessInJob_after_exit':mini.contains(identity.pid),'PID_in_active_job_list_after_exit':identity.pid in mini.process_ids(),'process_handle_retained':mini._process is not None}
        finally:
            if mini:mini.close();check('stdlib_minimal_child_cleaned',mini.cleanup_confirmed)
            for suffix in ('stdout.log','stderr.log'):
                path=root/('native-minimal.'+suffix)
                if path.exists():result['minimal_'+suffix]=b.read(path,65536).decode('utf-8','replace')
        result['IPC']=windows.ipc_experiment(plan,legacy._environment(root/'tmp'),check,label='native-probe');result['passed']=True
    except BaseException as ex:result.update(error_type=type(ex).__name__,safe_error=str(ex) if isinstance(ex,b.Stop) else 'sanitized')
    for suffix in ('stdout.log','stderr.log','start-receipt.json','finish-receipt.json'):
        p=root/('native-probe.'+suffix)
        if p.exists():result[suffix]=b.read(p,65536).decode('utf-8','replace')
    b.close_source_pin();result['source_handles_closed']=True
    b.write(b.HOME/'NATIVE-PROBE.json',result);print(json.dumps(result));return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
