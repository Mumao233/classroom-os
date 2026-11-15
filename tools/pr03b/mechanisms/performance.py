"""Real multi-process global PG admission and declared Windows screening."""
from __future__ import annotations
import ctypes as c,hashlib,json,os,secrets,statistics,threading,time,re,traceback
from pathlib import Path
from ctypes import wintypes as w
from concurrent.futures import ThreadPoolExecutor
import common as b,crypto,schema,windows
from datetime import datetime,timedelta,timezone
import admission
import request_diagnostics as rd
from service_pool import FixedPool

class OwnedCancellation:
    """Register once; an early failure cannot race past owned-Job cleanup."""
    def __init__(self):self.lock=threading.Lock();self.callback=None;self.cancelled=False;self.called=False
    def register(self,callback):
        with self.lock:
            b.require(self.callback is None,'owned_cancel_registered_twice');self.callback=callback
            invoke=self.cancelled and not self.called
            if invoke:self.called=True
        if invoke:callback()
    def cancel(self):
        with self.lock:
            self.cancelled=True;callback=self.callback;invoke=callback is not None and not self.called
            if invoke:self.called=True
        if invoke:callback()

class WorkerSchedule:
    """Prewarm fixed pools before the client chooses the measured window."""
    def __init__(self,count,offset):
        b.require(count in (1,2) and offset in (1000,1015),'fixed_worker_schedule')
        self.count=count;self.offset=offset;self.warm=0;self.lock=threading.Lock();self.ready=threading.Event();self.armed=threading.Event();self.start_at=None;self.cancelled=False;self.armed_while_ready=False
    def warm_and_start(self):
        with self.lock:
            self.warm+=1;b.require(self.warm<=self.count,'worker_warm_count')
            if self.warm==self.count:self.ready.set()
        return self.start()
    def start(self):
        b.require(self.armed.wait(120),'worker_window_arm_deadline')
        with self.lock:
            b.require(not self.cancelled and self.start_at is not None,'worker_window_cancelled')
            return self.start_at
    def arm(self,client_start):
        with self.lock:
            b.require(self.ready.is_set() and not self.cancelled and self.start_at is None,'worker_window_not_ready_or_already_armed')
            b.require(type(client_start) in (int,float) and time.monotonic()-.5<=client_start<=time.monotonic()+5,'worker_window_client_clock')
            self.start_at=client_start+self.offset;self.armed_while_ready=True;self.armed.set()
    def cancel(self):
        with self.lock:self.cancelled=True;self.armed.set()
    def evidence(self):
        with self.lock:return {'workers':self.count,'pools_warmed':self.warm,'offset':self.offset,'start_at':self.start_at,'cancelled':self.cancelled,'ready_before_arm':self.armed_while_ready}
def quantile(values,q):
    s=sorted(values);return s[min(len(s)-1,max(0,int(q*(len(s)-1))))] if s else None
def summary(values):return {'n':len(values),'p50':quantile(values,.5),'p95':quantile(values,.95),'p99':quantile(values,.99),'p99_claim_supported':len(values)>=1000}
def run_client(lease,plan,env,lab,scenario,*,seconds=1000,sessions=(),csrf=(),label,on_phase=None):
    """One reduced load process; private pipe is the only secret channel."""
    sd=c.c_void_p();size=w.DWORD();k,a=windows.api()
    b.require(a.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:P(A;;GA;;;SY)(A;;GA;;;'+windows.sid_string()+')',1,c.byref(sd),c.byref(size)),'load_pipe_descriptor')
    security=windows.Security(c.sizeof(windows.Security),sd,False);name=r'\\.\pipe\PR03b-S3-'+secrets.token_hex(16)
    h=k.CreateNamedPipeW(name,3|0x80000,4|2|8,1,65536,65536,3000,c.byref(security));k.LocalFree(sd)
    b.require(h not in (None,c.c_void_p(-1).value),'load_pipe_create')
    ready=threading.Event();done=threading.Event();box={};rows=[];job=identity=None
    def server():
        try:
            okay=k.ConnectNamedPipe(h,None);b.require(okay or c.get_last_error()==535,'load_pipe_connect');b.require(ready.wait(10),'load_process_identity_ready')
            pid=w.ULONG();b.require(k.GetNamedPipeClientProcessId(h,c.byref(pid)) and pid.value==identity.pid and job.contains(pid.value),'load_kernel_peer')
            b.require(windows.receive(k,h)=={'kind':'hello'},'load_hello')
            config={'scenario':scenario,'seconds':seconds,'port':lab.port,'ws_port':getattr(lab,'ws_port',None),'origin':lab.origin,'CA':(lab.root/'ca.pem').read_text(),'password':lab.password,'users':['load'+str(a) for a in schema.LOAD_ACTORS] if sessions else ['synthetic-student'],'sessions':list(sessions),'csrf':list(csrf)}
            windows.send(k,h,config)
            while True:
                value=windows.receive(k,h)
                if value['kind']=='rows':
                    b.require(set(value)=={'kind','rows'} and len(value['rows'])<=100,'load_rows_budget');rows.extend(value['rows']);windows.send(k,h,{'received':True})
                elif value['kind']=='phase':
                    expected='continuous_ready' if scenario=='mixed_continuous' else 'baseline_ready'
                    b.require(set(value)=={'kind','phase','start_monotonic'} and value['phase']==expected,'load_phase_shape')
                    if on_phase is not None:on_phase(value)
                    windows.send(k,h,{'received':True})
                else:
                    b.require(set(value)=={'kind','seconds','WS_opened'} and value['kind']=='done','load_final_shape');box.update(value);windows.send(k,h,{'received':True});break
            b.require(job.wait(10) and job.exit_code()==0,'load_client_exit')
            box['passed']=True
        except BaseException as ex:box.update(passed=False,error_type=type(ex).__name__,safe_error=str(ex) if isinstance(ex,b.Stop) else 'sanitized')
        finally:done.set()
    thread=threading.Thread(target=server,daemon=True);thread.start()
    try:
        driver='graded_driver.py' if scenario=='graded' else 'load_driver.py'
        job,identity=windows.start_python(plan,b.HOME/driver,[name,str(os.getpid())],env,label)
        ready.set();b.require(done.wait(seconds+180 if scenario in ('mixed','mixed_continuous') else 180),'fixed_load_client_watchdog')
        b.require(box.get('passed') is True,'fixed_load_client_failed:'+str(box.get('error_type')))
        return {'rows':rows,'summary':box,'client_PID':identity.pid,'client_creation':identity.creation_token}
    finally:
        k.CancelIoEx(h,None);k.DisconnectNamedPipe(h);k.CloseHandle(h)
        if job is not None:
            exit_observed=job.wait(0);exit_code=job.exit_code();job.close();thread.join(5)
            b.write(plan.run_root/(label+'.finish-receipt.json'),{'actual_exit_before_cleanup':exit_observed,'exit_code_before_cleanup':exit_code,'job_cleanup_confirmed':job.cleanup_confirmed is True,'pipe_server_stopped':not thread.is_alive(),'client_PID':identity.pid if identity else None,'database_execution_allowed':False,'server_result':{key:box[key] for key in ('passed','error_type','safe_error','seconds','WS_opened') if key in box}})
            b.require(not thread.is_alive() and job.cleanup_confirmed is True,'load_client_cleanup_unconfirmed')
        for offset in range(0,len(rows),1000):b.write(plan.run_root/('CAPACITY-CLIENT-RAW-'+label+'-'+str(offset//1000).zfill(4)+'.json'),{'rows':rows[offset:offset+1000],'client_completed':box.get('passed') is True})
def run_workers(lease,plan,env,seconds,threads,index_offset,*,start_at=None,worker_count=2,on_ready=None,standby=None):
    from argon2 import PasswordHasher
    phc=PasswordHasher(memory_cost=65536,time_cost=3,parallelism=1).hash(secrets.token_urlsafe(24));password=secrets.token_urlsafe(24)
    workers=[];all_rows=[];raw_files=[];peaks=[0,0];events=[];lock=threading.Lock();k,a=windows.api();start_at=start_at or time.monotonic()+10
    b.require(worker_count in (1,2),'fixed_worker_process_count')
    b.require(standby is None or (isinstance(standby,WorkerSchedule) and standby.count==worker_count),'fixed_standby_workers')
    for index in range(1,worker_count+1):
        sd=c.c_void_p();size=w.DWORD();sddl='D:P(A;;GA;;;SY)(A;;GA;;;'+windows.sid_string()+')'
        b.require(a.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,c.byref(sd),c.byref(size)),'hash_pipe_descriptor')
        security=windows.Security(c.sizeof(windows.Security),sd,False);name=r'\\.\pipe\PR03b-S3-'+secrets.token_hex(16)
        handle=k.CreateNamedPipeW(name,3|0x80000,4|2|8,1,65536,65536,3000,c.byref(security));k.LocalFree(sd)
        b.require(handle not in (None,c.c_void_p(-1).value),'hash_pipe_create');ready=threading.Event();done=threading.Event();box={}
        worker={'handle':handle,'ready':ready,'done':done,'box':box,'job':None,'identity':None,'index':index+index_offset}
        def server(worker=worker):
            diagnostic=rd.Request('other','other');diagnostic_token=rd.CURRENT.set(diagnostic)
            try:
                h=worker['handle'];ok=k.ConnectNamedPipe(h,None);b.require(ok or c.get_last_error()==535,'hash_pipe_connect');b.require(worker['ready'].wait(10),'hash_worker_identity')
                pid=w.ULONG();b.require(k.GetNamedPipeClientProcessId(h,c.byref(pid)) and pid.value==worker['identity'].pid and worker['job'].contains(pid.value),'hash_kernel_peer')
                b.require(windows.receive(k,h)=={'kind':'hello'},'hash_worker_hello')
                with FixedPool(lease,('auth',)) as pool:
                    for _ in range(2):b.require(pool.database('auth','SELECT 1',read_only=True)==1,'worker_prepublish_role_handshake')
                    configured_start=standby.warm_and_start() if standby is not None else start_at
                    windows.send(k,h,{'phc':phc,'password':password,'seconds':seconds,'threads':threads,'worker_index':worker['index'],'ingress_name':admission.shared().name,'start_at':configured_start})
                    while True:
                        v=windows.receive(k,h)
                        if v['kind']=='rows':
                            b.require(set(v)=={'kind','rows'} and len(v['rows'])<=200,'worker_results_budget')
                            with lock:all_rows.extend(v['rows'])
                            windows.send(k,h,{'received':True});continue
                        if v['kind']=='done':
                            b.require(set(v)=={'kind','loops'} and len(v['loops'])==threads,'worker_done_shape');worker['box']['loops']=v['loops'];windows.send(k,h,{'received':True});break
                        b.require(set(v)=={'kind','mode','id','deadline'} and v['kind']=='admit' and v['mode'] in ('enter','poll','finish','cancel') and type(v['id']) is int and type(v['deadline']) in (int,float),'worker_fixed_admission_message')
                        until=datetime.now(timezone.utc)+timedelta(seconds=min(1,max(0,v['deadline']-time.monotonic())))
                        # Even late packets retain their original expiry. A
                        # late grant must be explicitly finished, never hashed.
                        transaction_began=time.monotonic();expiry=v['deadline'] if v['mode'] in ('enter','poll') else time.monotonic()+5
                        context=admission.CURRENT.set(expiry)
                        try:
                            try:answer=pool.database('auth','SELECT identity_api.lab3_hash_admission(%s,%s,%s,%s,%s,%s)',(schema.base.ticket('auth',2),v['mode'],v['id'],pid.value,str(worker['identity'].creation_token),until))
                            except admission.Expired:
                                b.require(v['mode'] in ('enter','poll'),'hash_cleanup_deadline')
                                admission.reclaim_unstarted(lambda mode:pool.database('auth','SELECT identity_api.lab3_hash_admission(%s,%s,%s,%s,%s,%s)',(schema.base.ticket('auth',2),mode,v['id'],pid.value,str(worker['identity'].creation_token),until)),expensive_calls=0)
                                answer={'state':'rejected','active':0,'queued':0,'reason':'IPC_PG_ingress_deadline'}
                            except BaseException as ex:
                                if v['mode'] not in ('enter','poll') or getattr(ex,'sqlstate',None) not in ('57014','55P03'):raise
                                admission.reclaim_unstarted(lambda mode:pool.database('auth','SELECT identity_api.lab3_hash_admission(%s,%s,%s,%s,%s,%s)',(schema.base.ticket('auth',2),mode,v['id'],pid.value,str(worker['identity'].creation_token),until)),expensive_calls=0)
                                answer={'state':'rejected','active':0,'queued':0,'reason':'PG_ingress_timeout','sqlstate':ex.sqlstate}
                        finally:admission.CURRENT.reset(context)
                        answer['parent_pool_transaction_ms']=(time.monotonic()-transaction_began)*1000
                        with lock:peaks[0]=max(peaks[0],answer['active']);peaks[1]=max(peaks[1],answer['queued'])
                        windows.send(k,h,answer)
                b.require(worker['job'].wait(10),'hash_worker_process_exit');worker['box']['exit_code']=worker['job'].exit_code()
            except BaseException as ex:
                rd.failure(ex,'worker.control')
                message=str(ex)
                safe=message if (isinstance(ex,b.Stop) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,180}',message)) or (message.startswith('PR-03b guard:') and len(message)<=240 and '\n' not in message) else 'sanitized'
                worker['box'].update(error_type=type(ex).__name__,safe_error=safe,sqlstate=getattr(ex,'sqlstate',None),frames=[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(ex.__traceback__)])
            finally:
                try:
                    diagnostic.handler_finished_ms=round((time.monotonic()-diagnostic.start)*1000,3)
                    name='R02-WORKER-DIAGNOSTICS-'+str(worker['index'])+'-'+secrets.token_hex(8)+'.json'
                    b.write(plan.run_root/name,diagnostic.snapshot())
                except BaseException as ex:worker['box'].update(diagnostic_write_error_type=type(ex).__name__)
                finally:rd.CURRENT.reset(diagnostic_token);worker['done'].set()
        t=threading.Thread(target=server,daemon=True);worker['thread']=t;t.start()
        try:job,identity=windows.start_python(plan,b.HOME/'perf_worker.py',[name,str(os.getpid())],env,'hash-worker-'+str(worker['index']))
        except BaseException:
            if standby is not None:standby.cancel()
            # A failed second launch must still close the first owned Job.
            # The failing launch itself is cleaned by windows.start_python.
            cleanup=[]
            for pending in [*workers,worker]:
                h=pending['handle'];k.CancelIoEx(h,None);k.DisconnectNamedPipe(h);k.CloseHandle(h)
                record={'worker_index':pending['index']}
                if pending['job'] is not None:
                    try:pending['job'].close();record['job_cleanup_confirmed']=pending['job'].cleanup_confirmed is True
                    except BaseException as ex:record['cleanup_error_type']=type(ex).__name__
                cleanup.append(record)
            b.write(plan.run_root/('CAPACITY-WORKER-START-FAIL-'+str(index_offset)+'.json'),{'cleanup':cleanup})
            raise
        worker.update(job=job,identity=identity);ready.set();workers.append(worker)
    try:
        def cancel_owned():
            if standby is not None:standby.cancel()
            for worker in workers:worker['job'].close()
        if on_ready is not None:on_ready(cancel_owned)
        if standby is not None:start_at=standby.start()
        deadline=max(time.monotonic(),start_at)+seconds+120
        for worker in workers:b.require(worker['done'].wait(max(.01,deadline-time.monotonic())),'hash_worker_watchdog')
        b.require(all(w['box'].get('exit_code')==0 and 'diagnostic_write_error_type' not in w['box'] for w in workers),'hash_worker_exit_not_zero')
        lifetimes=[]
        for worker in workers:
            created,exited,kernel,user=(w.FILETIME() for _ in range(4))
            b.require(worker['job'].api.k.GetProcessTimes(worker['job']._process,c.byref(created),c.byref(exited),c.byref(kernel),c.byref(user)),'hash_retained_HANDLE_process_times')
            stamp=lambda value:(value.dwHighDateTime<<32)|value.dwLowDateTime
            start=stamp(created);end=stamp(exited)
            b.require(start==worker['identity'].creation_token and end>start and (end-start)/10000000>=seconds,'hash_worker_actual_lifetime_too_short')
            loops=worker['box']['loops']
            b.require(all(v['loop_start']>=start_at and v['loop_start']<start_at+5 and v['loop_end']>=start_at+seconds for v in loops),'hash_worker_actual_load_window')
            lifetimes.append({'PID':worker['identity'].pid,'creation_token':start,'actual_seconds':(end-start)/10000000,'requested_seconds':seconds,'exit_code':worker['box']['exit_code'],'loops':loops})
        b.write(plan.run_root/('CAPACITY-WORKER-LIFETIMES-'+str(index_offset)+'.json'),{'rows':lifetimes})
        return {'rows':all_rows,'raw_files':raw_files,'max_active':peaks[0],'max_queued':peaks[1],'workers':worker_count,'separate_processes':worker_count==2,'native_PID_creation':[(w['identity'].pid,w['identity'].creation_token) for w in workers],'native_lifetimes':lifetimes}
    finally:
        cleanup=[];errors=[]
        for worker in workers:
            record={'PID':worker['identity'].pid,'server_result':{key:worker['box'][key] for key in ('error_type','safe_error','sqlstate','frames','exit_code') if key in worker['box']}}
            try:
                h=worker['handle'];k.CancelIoEx(h,None);k.DisconnectNamedPipe(h);k.CloseHandle(h)
                record.update(exit_observed_before_cleanup=worker['job'].wait(0),exit_code_before_cleanup=worker['job'].exit_code())
            except BaseException as ex:record['observation_error_type']=type(ex).__name__;errors.append(type(ex).__name__)
            finally:
                try:worker['job'].close();record['job_cleanup_confirmed']=worker['job'].cleanup_confirmed is True
                except BaseException as ex:record['cleanup_error_type']=type(ex).__name__;errors.append(type(ex).__name__)
                worker['thread'].join(5);record['pipe_server_stopped']=not worker['thread'].is_alive()
                if not record['pipe_server_stopped']:errors.append('pipe_server_not_stopped')
                cleanup.append(record)
        b.write(plan.run_root/('CAPACITY-WORKER-CLEANUP-'+str(index_offset)+'.json'),{'workers':cleanup,'errors':errors})
        with lock:captured=list(all_rows)
        for offset in range(0,len(captured),1000):
            name='PERF-ROWS-'+str(index_offset)+'-'+str(offset//1000).zfill(5)+'.json'
            b.write(plan.run_root/name,{'rows':captured[offset:offset+1000],'worker_cleanup_confirmed':not errors});raw_files.append(name)
        b.require(not errors and all(v.get('job_cleanup_confirmed') is True for v in cleanup),'hash_worker_cleanup_unconfirmed')
def experiments(lease,plan,env,check):
    import psutil
    stop=threading.Event();samples=[]
    def sample():
        psutil.cpu_percent(None)
        while not stop.wait(.5):
            memory=psutil.virtual_memory()
            samples.append({'seconds':round(time.monotonic()-began,3),'available_bytes':memory.available,'total_bytes':memory.total,'guest_CPU_percent':psutil.cpu_percent(None),'coordinator_RSS':psutil.Process(os.getpid()).memory_info().rss})
    began=time.monotonic();thread=threading.Thread(target=sample,daemon=True);thread.start()
    try:return _experiments(lease,plan,env,check)
    finally:
        stop.set();thread.join(5)
        b.write(plan.run_root/'RESOURCE-SAMPLES.json',{'samples':samples,'sample_interval_seconds':.5,'sampled_min_available_fraction':min((x['available_bytes']/x['total_bytes'] for x in samples),default=None),'hard_SUT_client_resource_groups_separated':False,'scope':'whole guest samples, not deployment capacity or continuous hard-budget proof'})
def _experiments(lease,plan,env,check):
    import psutil
    from argon2 import PasswordHasher
    screening=[];candidates=[('H-A',19456,2,1),('H-B',65536,2,1),('H-C',65536,3,1),('H-D',65536,3,4),('H-E',131072,3,1),('H-F',65536,4,1)]
    for name,m,t,p in candidates:
        hasher=PasswordHasher(memory_cost=m,time_cost=t,parallelism=p);value=secrets.token_urlsafe(24);phc=hasher.hash(value)
        for concurrency in (1,2,4,8):
            for kind,password in (('correct',value),('wrong','not-the-password'),('dummy','unknown-account-input')):
                def task(_):
                    start=time.perf_counter();valid=crypto.verify(phc,password);return (time.perf_counter()-start)*1000,valid
                with ThreadPoolExecutor(max_workers=concurrency) as pool:rows=list(pool.map(task,range(16)))
                screening.append({'candidate':name,'m_kib':m,'t':t,'p':p,'concurrency':concurrency,'kind':kind,**summary([v[0] for v in rows])})
                check('Argon2_correct_wrong_dummy:'+name+':'+str(concurrency)+':'+kind,all(v[1] is (kind=='correct') for v in rows))
    initial=run_workers(lease,plan,env,15,16,0)
    b.write(b.HOME/'evidence/PERF-initial.json',{'complete':True,'rows':len(initial['rows']),'seconds_requested':15})
    check('hash_burst_positive_and_rejection_controls',any(r['allowed'] for r in initial['rows']) and any(not r['allowed'] for r in initial['rows']))
    check('hash_budget_two_real_processes',initial['separate_processes'])
    check('hash_global_2_slots_8_queue',initial['max_active']<=2 and initial['max_queued']<=8)
    check('hash_rejected_no_expensive_work',all(r['expensive_calls']==0 for r in initial['rows'] if not r.get('actually_admitted',r['allowed'])))
    # Required real 10-minute overload, followed by 2-minute recovery.
    overload=run_workers(lease,plan,env,600,16,10)
    b.write(b.HOME/'evidence/PERF-overload.json',{'complete':True,'rows':len(overload['rows']),'seconds_requested':600})
    recovery_start=time.monotonic();recovery=run_workers(lease,plan,env,120,1,20)
    with lease.connect('bootstrap') as c:remaining=c.execute('SELECT count(*) FROM identity_control.lab3_hash_jobs').fetchone()[0]
    check('hash_no_slot_leak_after_workers',remaining==0)
    check('hash_no_cost_lowering_overload',overload['max_active']<=2 and overload['max_queued']<=8 and all(r['expensive_calls']==0 for r in overload['rows'] if not r.get('actually_admitted',r['allowed'])))
    rejected=[r['ms'] for r in overload['rows'] if not r.get('actually_admitted',r['allowed'])];success=[r['hash_ms'] for r in recovery['rows'] if r['allowed']]
    by_reason={reason:summary([r['ms'] for r in overload['rows'] if r['reason']==reason]) for reason in sorted({r['reason'] for r in overload['rows']})}
    full=[r['ms'] for r in overload['rows'] if r['reason'] in ('ingress_full','queue_full')]
    report={'evidence_level':'Windows_guest_screening','screening':screening,'initial':initial,'overload':overload,'recovery':recovery,
        'rejected_ms':summary(rejected),'full_queue_reject_ms':summary(full),'rejections_by_reason':by_reason,'recovery_hash_ms':summary(success),'RAM_effective_bytes':psutil.virtual_memory().total,
        'target_deployment_capacity_passed':False,'CPU_RAM_groups_separated':False,'normal_30_account_30_WS_mixed_load_run':False,
        'limitations':['Guest screening does not establish E-L/E-T service/client hard-budget isolation or production capacity.','Per-configuration screening N=16 cannot support p99 pass.']}
    reject_stats=report['full_queue_reject_ms']
    report['candidate_reject_500ms']={'threshold_ms':500,'sample_p99_ms':reject_stats['p99'],'n':len(full),'result':'FAIL' if full and reject_stats['p99']>500 else 'INCONCLUSIVE','deployment_PASS':False}
    b.write(plan.run_root/'PERFORMANCE.json',report)
    return {k:v for k,v in report.items() if k not in ('initial','overload','recovery','screening')}
