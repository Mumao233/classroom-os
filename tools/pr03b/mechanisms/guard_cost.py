"""Fixed, fresh guard-component timing; no cached observations or SQL."""
from concurrent.futures import ThreadPoolExecutor
import threading,time
import common as b

def experiments(lease,plan,check,env=None):
    import native_process as native,target_guard as guard
    rows=[];lock=threading.Lock()
    bridge=getattr(guard.observe_windows_process,'s3_identity_bridge',None)
    if (b.HOME/'native_identity.dll').is_file():b.require(bridge is not None,'native_identity_adapter_not_installed')
    if bridge is not None:
        import windows,native_identity,os
        original=guard.observe_windows_process.s3_original_observer
        original_handle=guard.observe_windows_process.s3_original_handle_observer
        b.require(env is not None,'native_identity_control_fixed_environment')
        control=[];job=None
        try:
            job,identity=windows.start_python(plan,b.HOME/'native_identity_probe.py',[],env,'native-identity-probe')
            token=job.token.process(identity.pid)
            check('native_identity_probe_reduced_token',token['administrative_groups_enabled'] is False)
            for repeat in range(20):
                now=guard.observe_windows_process(os.getpid());before=original(os.getpid())
                by_handle=native._handle_identity(job.api,job._process,identity.pid)
                old_handle=original_handle(job.api,job._process,identity.pid)
                check('native_identity_original_live_equivalence:'+str(repeat),now==before and by_handle==old_handle==identity)
            check('native_identity_probe_normal_exit',job.wait(10) and job.exit_code()==0)
            rejected=[]
            for label,operation in (('retained_exited_HANDLE',lambda:bridge.handle(job._process,identity.pid)),('exited_PID',lambda:bridge.pid(identity.pid))):
                try:operation()
                except native_identity.Rejected:rejected.append(label)
            check('native_identity_exited_process_rejected',rejected==['retained_exited_HANDLE','exited_PID'])
            control=[{'PID':identity.pid,'creation_token':identity.creation_token,'administrative_groups_enabled':token['administrative_groups_enabled'],'normal_exit_code':0,'retained_handle_and_PID_rejected_after_exit':True,'original_live_comparisons':20}]
        finally:
            if job is not None:
                job.close();b.require(job.cleanup_confirmed,'native_identity_probe_cleanup')
        b.write(plan.run_root/'R02-NATIVE-IDENTITY-CONTROLS.json',{'rows':control,'job_cleanup_confirmed':True,'SQL_executed':False,'facts_cached':False})
    def config():
        for name,digest in lease.pins.items():
            b.require(b.sha(guard._read_file(plan.data_directory/name,32768))==digest,'guard_cost_configuration')
    components=(('configuration',config),
        ('owned_endpoint',lambda:native.verify_owned_endpoint(plan,lease.process,lease.job.parent,lease.job)),
        ('local_ownership',lambda:guard.verify_local_ownership(plan)),
        ('job_network',lambda:native.job_network(lease.job)),
        ('source_bundle',b.bundle),
        ('restricted_token',lambda:lease.job.token.process(lease.process.pid)),
        ('whole_original_boundary',lease._local))
    def run(index,parallel):
        for label,action in components:
            for repeat in range(3):
                start=time.perf_counter();cpu=time.thread_time();action()
                row={'parallel':parallel,'index':index,'repeat':repeat,'component':label,'seconds':time.perf_counter()-start,'thread_cpu_seconds':time.thread_time()-cpu}
                with lock:rows.append(row)
    try:
        for parallel in (1,4):
            with ThreadPoolExecutor(max_workers=parallel) as workers:
                list(workers.map(lambda index:run(index,parallel),range(parallel)))
        check('guard_component_all_fresh_checks',len(rows)==105)
        # Fixed non-request diagnostics only; never dump locals, arguments,
        # stacks or profile files. No performance threshold uses these rows.
        import cProfile,pstats
        from pathlib import Path
        profile=cProfile.Profile()
        try:
            profile.enable()
            for _ in range(30):lease._local()
        finally:profile.disable()
        stats=pstats.Stats(profile);functions=[]
        for (file,line,name),(primitive,total,self_s,cumulative,callers) in stats.stats.items():
            functions.append({'file':Path(file).name,'line':line,'function':name,'primitive_calls':primitive,'total_calls':total,'self_seconds':self_s,'cumulative_seconds':cumulative})
        b.write(plan.run_root/'R02-GUARD-PROFILE.json',{'repeats':30,'functions':sorted(functions,key=lambda r:r['self_seconds'],reverse=True),'arguments_or_locals_recorded':False,'acceptance_sample':False})
        return {'rows':rows,'observations_cached':False,'SQL_executed':False}
    finally:b.write(plan.run_root/'R02-GUARD-COMPONENTS.json',{'rows':rows,'observations_cached':False,'SQL_executed':False})
