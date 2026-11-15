"""One preregistered R02/R04 package; fixed counts and hard guest limits."""
from datetime import datetime,timedelta,timezone
import os,threading,time,statistics
import common as b,schema as s,transport,performance,resource_group,evidence_stats,admission

def actual_hash_windows(rows,start,seconds):
    counts=[0 for _ in range(0,seconds,10)]
    for row in rows:
        if row['expensive_calls']!=1:continue
        at=row['started_monotonic']+row['queue_ms']/1000
        if start<=at<start+seconds:counts[int((at-start)//10)]+=1
    return counts

def save_rows(plan,label,rows):
    names=[]
    for offset in range(0,len(rows),1000):
        name='CAPACITY-ROWS-'+label+'-'+str(offset//1000).zfill(4)+'.json'
        b.write(plan.run_root/name,{'rows':rows[offset:offset+1000]});names.append(name)
    return names
def stats(rows,kind):
    selected=[r for r in rows if r['kind']==kind]
    times=[r['ms'] for r in selected]
    return {'offered':len(selected),'completed':sum(r['status'] is not None for r in selected),'successes':sum(r['status']==200 and (kind not in ('login','nat') or r.get('authenticated') is True) for r in selected),'rejects':sum(r['status'] in (403,429) for r in selected),'timeouts':sum(r['timeout'] for r in selected),'errors':sum('error_type' in r for r in selected),'ms':performance.summary(times),'quantile_rank_95_intervals':{str(q):evidence_stats.quantile_interval(times,q) for q in (.5,.95,.99)},'CI_limit':'binomial rank interval assumes independent observations; shared-load serial dependence can widen uncertainty; no deployment or CI-threshold acceptance claimed','dispatch_lag_ms':performance.summary([r['dispatch_lag_ms'] for r in selected])}

def queue_recovery(rows,start):
    baseline=[r['queued'] for r in rows if start<=r['monotonic']<start+1000]
    target=performance.quantile(baseline,.95);windows=[]
    for index,row in enumerate(rows):
        if row['monotonic']<start+1600:continue
        group=[]
        for end in rows[index:]:
            group.append(end)
            if end['monotonic']-row['monotonic']>=10:break
        if group[-1]['monotonic']-row['monotonic']<10:continue
        windows.append({'start':row['monotonic']-start,'end':group[-1]['monotonic']-start,'N':len(group),'p95':performance.quantile([v['queued'] for v in group],.95),'coverage':max(v['monotonic']-u['monotonic'] for u,v in zip(group,group[1:]))<=5})
    first=next((r['end']-1600 for r in windows if r['coverage'] and target is not None and r['p95']<=target),None)
    recovery=[r for r in windows if r['start']>=1660 and r['end']<=1780]
    coverage=target is not None and bool(recovery) and recovery[0]['start']<=1665 and recovery[-1]['end']>=1775 and all(r['coverage'] for r in recovery)
    return {'baseline_p95':target,'first_10s_stable_window_end_seconds':first,'recovery_2min_covered':coverage,'recovery_windows_meet_baseline':coverage and all(r['p95']<=target for r in recovery),'windows':windows,'max_observation_gap_seconds':5}

def exact_queue(lease,plan,check):
    import native_process
    wid=os.getpid();ct=str(native_process.observe_process_details(wid).identity.creation_token);rows=[]
    with lease.connect('auth') as a,lease.connect('auth') as z:
        def invoke(c,mode,jid):return c.execute('SELECT identity_api.lab3_hash_admission(%s,%s,%s,%s,%s)',(s.ticket('auth',2),mode,jid,wid,ct)).fetchone()[0]
        for repeat in range(20):
            ids=[700000000+repeat*20+i for i in range(11)]
            # Pre-open both transactions. The second really sees committed
            # reservations from a DIFFERENT connection, not uncommitted tuples.
            z.execute('BEGIN READ WRITE');a.execute('BEGIN READ WRITE')
            entered=[invoke(a,'enter',jid) for jid in ids[:10]];a.execute('COMMIT')
            began=time.monotonic();full=invoke(z,'enter',ids[10]);ms=(time.monotonic()-began)*1000;z.execute('COMMIT')
            check('R04_actual_full_8_queue:'+str(repeat),[v['state'] for v in entered]==['active']*2+['queued']*8 and full['state']=='rejected' and full['reason']=='queue_full' and full['active']==2 and full['queued']==8)
            a.execute('BEGIN READ WRITE')
            for jid in ids[:2]:invoke(a,'finish',jid)
            for jid in ids[2:10]:invoke(a,'cancel',jid)
            a.execute('COMMIT')
            rows.append({'repeat':repeat,'different_connections':True,'full':full,'fixed_SQL_ms':ms,'expensive_calls':0})
    b.write(plan.run_root/'CAPACITY-EXACT-QUEUE.json',{'rows':rows,'scope':'dedicated reservation/barrier control; actual Argon2 overload measured separately'})
    return {'repeats':20,'max_active':2,'max_queued':8,'rows_file':'CAPACITY-EXACT-QUEUE.json'}

def admission_commit_loss(lease,plan,check):
    import native_process
    from service_pool import FixedPool
    wid=os.getpid();ct=str(native_process.observe_process_details(wid).identity.creation_token);rows=[]
    with FixedPool(lease,('auth',)) as pool,lease.connect('bootstrap') as observer:
        for repeat in range(20):
            jid=800000000+repeat;until=datetime.now(timezone.utc)+timedelta(seconds=1)
            def control(mode):return pool.database('auth','SELECT identity_api.lab3_hash_admission(%s,%s,%s,%s,%s,%s)',(s.ticket('auth',2),mode,jid,wid,ct,until))
            value=control('enter')
            check('R02_unstarted_real_commit_before_response_loss:'+str(repeat),value['state']=='active' and observer.execute('SELECT state FROM identity_control.lab3_hash_jobs WHERE id=%s',(jid,)).fetchone()==('active',))
            # Fixed delivery seam: the successful answer is withheld. Expiry
            # cannot itself free an active reservation or execute the hash.
            token=admission.CURRENT.set(time.monotonic()-1)
            try:admission.reclaim_unstarted(control,expensive_calls=0)
            finally:admission.CURRENT.reset(token)
            check('R02_lost_unstarted_answer_reclaimed_owned_only:'+str(repeat),observer.execute('SELECT count(*) FROM identity_control.lab3_hash_jobs WHERE id=%s',(jid,)).fetchone()[0]==0)
            rows.append({'repeat':repeat,'committed_active_before_drop':True,'expensive_calls':0,'caller_deadline_expired':True,'reservation_removed_after_owned_resolution':True})
    b.write(plan.run_root/'CAPACITY-ADMISSION-COMMIT-LOSS.json',{'rows':rows,'fault':'fixed answer-delivery seam after actual SQL commit, before hash starts','executing_task_early_release':False})
    return {'repeats':20,'actual_PG_commit':True,'hashes_started':0}

def experiments(lease,plan,env,check):
    limits=resource_group.CURRENT;b.require(limits is not None and limits.contains(lease.process.pid),'hard_resource_groups_and_owned_PG_required')
    prereg={'scenario':'single_integrated_R02_R04_package','hash':{'m_kib':65536,'t':3,'p':1},'SUT_CPU':2,'SUT_RAM_bytes':4*1024**3,'load_CPU':1,'load_RAM_bytes':1024**3,'normal_users':30,'normal_WS':30,'business_per_second':10,'login_per_second':1,'baseline_seconds':1000,'normal_seconds':1000,'overload_seconds':600,'recovery_seconds':120,'NAT_logins':60,'NAT_spacing_seconds':1,'preauth_source_per_minute':90,'preauth_global_per_minute':120,'main_p99_minimum_N':1000,'queue_slots':8,'hash_slots':2,'max_queue_seconds':1,'full_queue_reject_p99_ms':500,'login_p95_ms':1000,'login_p99_ms':2000,'hash_p95_ms':500,'hash_p99_ms':1000,'business_p95_degradation_max':.2,'memory_fraction_max':.75,'recovery_to_baseline_seconds_max':60,'sample_reduction_allowed':False,'stop_rules':['guard/ownership/isolation failure','owned process or resource containment failure','watchdog expiry; preserve actual partial counts and STOP'],'ordinary_call_budget_seconds':5}
    prereg.update(continuous_seconds=1780,overload_starts_second=1000,recovery_transition_seconds=60,burst_clients=[16,32],worker_process_variants=[1,2],normal_load_held_during_overload=True,queue_recovery_window_seconds=10,queue_observation_max_gap_seconds=5,quantile_algorithm='nearest lower order statistic; binomial rank uncertainty separately')
    prereg.update(service_pool_channels={name:transport.FixedPool.pool_size(name) for name in ('auth','session','runtime')},actual_hash_overload_observation_window_seconds=10,actual_hashes_required_in_every_overload_window=True,baseline_idle_hash_workers=2,baseline_queue_observer=True,baseline_32_thread_burst_starts_second=1015,baseline_worker_burst_seconds=15,baseline_and_normal_same_idle_worker_and_PG_connection_counts=True)
    prereg.update(idle_worker_PG_pools_and_queue_observer_ready_before_measured_window=True,client_selects_window_clock_after_readiness=True)
    b.write(plan.run_root/'CAPACITY-PREREGISTRATION.json',prereg)
    report={'preregistration':prereg,'threshold_failures':[]};samples=[];stop=threading.Event()
    def sample():
        try:
            while not stop.wait(1):samples.append({'monotonic':time.monotonic(),**limits.verify(),**limits.resident_sample()})
        except BaseException as ex:report['resource_monitor_error']=type(ex).__name__
    thread=threading.Thread(target=sample,daemon=True);thread.start()
    def requirement(name,ok):
        if not ok:report['threshold_failures'].append(name)
    try:
        report['exact_queue']=exact_queue(lease,plan,check)
        report['unstarted_commit_loss']=admission_commit_loss(lease,plan,check)
        bursts=[]
        for processes in (1,):
            burst=performance.run_workers(lease,plan,env,15,16,60+processes*10,worker_count=processes)
            check('R02_burst_global_slots:'+str(processes),burst['max_active']<=2 and burst['max_queued']<=8 and all(r['expensive_calls']==0 for r in burst['rows'] if not r['actually_admitted']))
            check('R02_burst_actual_hash_control:'+str(processes),any(r['expensive_calls']==1 for r in burst['rows']))
            bursts.append({'processes':processes,'simultaneous_threads':16*processes,'actual_lifetimes':burst['native_lifetimes'],'raw_files':burst['raw_files']})
        report['bursts']=bursts
        root=b.HOME/'R04-NAT-TLS-private';root.mkdir();lab=transport.Lab(lease,root)
        try:
            nat=performance.run_client(lease,plan,env,lab,'nat',label='R04-NAT-client')
            report['NAT']={**stats(nat['rows'],'nat'),'seconds':nat['summary']['seconds'],'raw_files':save_rows(plan,'NAT',nat['rows'])}
            requirement('60_full_NAT_logins_at_1_per_second',report['NAT']['successes']==60 and max(r['arrived_s']+r['ms']/1000 for r in nat['rows'])<=60)
        finally:lab.close()
        b.require(not report['threshold_failures'],'R04_NAT_prerequisite_failed')
        root=b.HOME/'R04-EXCESS-TLS-private';root.mkdir();lab=transport.Lab(lease,root)
        try:
            excess=performance.run_client(lease,plan,env,lab,'preauth_excess',label='R04-preauth-client')
            values=[r['status'] for r in sorted(excess['rows'],key=lambda r:r['index'])]
            report['preauth_excess']={'rows_files':save_rows(plan,'preauth',excess['rows']),'successes':values.count(200),'rejects':values.count(429),'seconds':excess['summary']['seconds']}
            requirement('HTTP_source_budget_exhaustion_90',values.count(200)==90 and values.count(429)==31 and excess['summary']['seconds']<60)
        finally:lab.close()
        b.require(not report['threshold_failures'],'R04_preauth_prerequisite_failed')
        with lease.connect('auth') as c:
            c.execute('BEGIN READ WRITE')
            for i in range(120):b.require(c.execute('SELECT identity_api.lab3_fail_budget(%s,%s,120,60,true)',(s.ticket('auth',2),'R04-global-fixed-control')).fetchone()[0]['allowed'],'global_budget_early_reject')
            end=c.execute('SELECT identity_api.lab3_fail_budget(%s,%s,120,60,true)',(s.ticket('auth',2),'R04-global-fixed-control')).fetchone()[0]
            check('R04_global_creation_120_SQL_control',not end['allowed'] and end['n']==120);c.execute('COMMIT')
        root=b.HOME/'R04-MIXED-TLS-private';root.mkdir();lab=transport.Lab(lease,root,business=True)
        try:
            lab.install_load_accounts();lab.ws_start();tokens=[];csrf=[]
            for actor in s.LOAD_ACTORS:
                token,row=lab.login(actor,password_version=1);tokens.append(token);csrf.append(row['csrf'])
            mixed={};report['mixed']=mixed;lab.no_hash_baseline=True
            baseline_work={};baseline_done=threading.Event();baseline_queue_stop=threading.Event();baseline_queue=[]
            baseline_cancel=performance.OwnedCancellation();baseline_schedule=performance.WorkerSchedule(2,1015);baseline_queue_ready=threading.Event()
            def prepare_baseline():
                def worker_task():
                    try:baseline_work['burst']=performance.run_workers(lease,plan,env,15,16,80,worker_count=2,on_ready=baseline_cancel.register,standby=baseline_schedule)
                    except BaseException as ex:baseline_work['worker_error_type']=type(ex).__name__
                    finally:baseline_done.set()
                baseline_work['worker_thread']=threading.Thread(target=worker_task,daemon=True);baseline_work['worker_thread'].start()
                def observe():
                    try:
                        with lease.connect('bootstrap') as c:
                            baseline_queue_ready.set()
                            while not baseline_queue_stop.wait(1):
                                c.execute('BEGIN READ ONLY');a,q=c.execute("SELECT count(*) FILTER(WHERE state='active'),count(*) FILTER(WHERE state='queued') FROM identity_control.lab3_hash_jobs").fetchone();c.execute('COMMIT')
                                baseline_queue.append({'monotonic':time.monotonic(),'active':a,'queued':q})
                    except BaseException as ex:baseline_work['queue_error_type']=type(ex).__name__
                baseline_work['queue_thread']=threading.Thread(target=observe,daemon=True);baseline_work['queue_thread'].start()
                b.require(baseline_schedule.ready.wait(30) and baseline_queue_ready.wait(30),'baseline_fixed_workers_and_observer_not_ready')
            def start_baseline(value):
                baseline_work['start']=value['start_monotonic'];baseline_schedule.arm(value['start_monotonic'])
            try:
                prepare_baseline()
                trial=performance.run_client(lease,plan,env,lab,'mixed',seconds=1000,sessions=tokens,csrf=csrf,label='R04-baseline-client',on_phase=start_baseline)
                b.require(baseline_done.wait(60) and 'burst' in baseline_work,'baseline_standby_worker_completion_failed')
            finally:
                baseline_queue_stop.set()
                if 'queue_thread' in baseline_work:baseline_work['queue_thread'].join(10)
                if 'worker_thread' in baseline_work and not baseline_done.is_set():
                    baseline_schedule.cancel();baseline_cancel.cancel();baseline_done.wait(30)
                b.write(plan.run_root/'CAPACITY-BASELINE-WORKER-CONTROL.json',{'start_monotonic':baseline_work.get('start'),'planned_burst_offset':1015,'actual_burst':baseline_work.get('burst',{}).get('native_lifetimes'),'worker_error_type':baseline_work.get('worker_error_type'),'queue_error_type':baseline_work.get('queue_error_type'),'queue_samples':baseline_queue,'worker_finished':baseline_done.is_set(),'readiness':baseline_schedule.evidence(),'queue_observer_ready':baseline_queue_ready.is_set()})
                if 'worker_thread' in baseline_work:
                    baseline_work['worker_thread'].join(5)
                    b.require(baseline_done.is_set() and not baseline_work['worker_thread'].is_alive(),'baseline_owned_workers_not_drained')
            b.require('queue_error_type' not in baseline_work and not baseline_work['queue_thread'].is_alive(),'baseline_queue_observer_failed')
            burst=baseline_work['burst']
            check('R02_burst_global_slots:2',burst['max_active']<=2 and burst['max_queued']<=8 and all(r['expensive_calls']==0 for r in burst['rows'] if not r['actually_admitted']))
            check('R02_burst_actual_hash_control:2',any(r['expensive_calls']==1 for r in burst['rows']))
            bursts.append({'processes':2,'simultaneous_threads':32,'actual_lifetimes':burst['native_lifetimes'],'raw_files':burst['raw_files'],'scheduled_after_nohash_requests':True})
            mixed['baseline']={'business':stats(trial['rows'],'business'),'login':stats(trial['rows'],'login'),'WS':trial['summary']['WS_opened'],'seconds':trial['summary']['seconds'],'raw_files':save_rows(plan,'baseline',trial['rows'])}
            requirement('baseline_full_samples_and_zero_unexpected_denial',mixed['baseline']['business']['successes']==10000 and mixed['baseline']['login']['successes']==1000 and mixed['baseline']['WS']==30)
            b.require(not report['threshold_failures'],'R04_baseline_prerequisite_failed')
            # Drain already-started tasks, retaining their original slot until
            # completion. There is no forced cancellation or budget reset.
            until=time.monotonic()+10
            while lab.hash_gate.outstanding and time.monotonic()<until:time.sleep(.02)
            b.require(lab.hash_gate.outstanding==0,'baseline_tasks_not_drained')
            lab.no_hash_baseline=False;internal_offset=len(lab.admission_rows)
            work={};worker_done=threading.Event();queue_samples=[];queue_stop=threading.Event()
            continuous_schedule=performance.WorkerSchedule(2,1000);continuous_cancel=performance.OwnedCancellation();continuous_queue_ready=threading.Event()
            def prepare_continuous():
                def overload_task():
                    try:work['overload']=performance.run_workers(lease,plan,env,600,16,40,on_ready=continuous_cancel.register,standby=continuous_schedule)
                    except BaseException as ex:
                        work.update(error_type=type(ex).__name__,safe_error=str(ex) if isinstance(ex,b.Stop) else 'sanitized')
                        report['overload_worker_failure']={key:work[key] for key in ('error_type','safe_error')}
                        b.write(plan.run_root/'CAPACITY-OVERLOAD-WORKER-FAILURE.json',report['overload_worker_failure'])
                    finally:worker_done.set()
                work['worker_thread']=threading.Thread(target=overload_task,daemon=True);work['worker_thread'].start()
                def observe_queue():
                    try:
                        with lease.connect('bootstrap') as c:
                            continuous_queue_ready.set()
                            while not queue_stop.wait(1):
                                c.execute('BEGIN READ ONLY');a,q=c.execute("SELECT count(*) FILTER(WHERE state='active'),count(*) FILTER(WHERE state='queued') FROM identity_control.lab3_hash_jobs").fetchone();c.execute('COMMIT')
                                queue_samples.append({'monotonic':time.monotonic(),'active':a,'queued':q})
                    except BaseException as ex:work['queue_error_type']=type(ex).__name__
                work['queue_thread']=threading.Thread(target=observe_queue,daemon=True);work['queue_thread'].start()
                b.require(continuous_schedule.ready.wait(30) and continuous_queue_ready.wait(30),'continuous_fixed_workers_and_observer_not_ready')
            def start_continuous(value):
                work['start']=value['start_monotonic'];continuous_schedule.arm(value['start_monotonic'])
            try:
                prepare_continuous()
                continuous=performance.run_client(lease,plan,env,lab,'mixed_continuous',seconds=1780,sessions=tokens,csrf=csrf,label='R04-continuous-client',on_phase=start_continuous)
                b.require(worker_done.wait(10) and 'overload' in work,'continuous_overload_worker_failed:'+str(work.get('error_type')))
            finally:
                queue_stop.set()
                if 'queue_thread' in work:work['queue_thread'].join(10)
                if 'worker_thread' in work and not worker_done.is_set():
                    continuous_schedule.cancel();continuous_cancel.cancel();worker_done.wait(30)
                b.write(plan.run_root/'CAPACITY-QUEUE-SAMPLES.json',{'rows':queue_samples,'start_monotonic':work.get('start'),'overload_end_offset':1600,'recovery_start_offset':1660,'error_type':work.get('queue_error_type'),'readiness':continuous_schedule.evidence(),'queue_observer_ready':continuous_queue_ready.is_set()})
                if 'worker_thread' in work:
                    work['worker_thread'].join(5)
                    b.require(worker_done.is_set() and not work['worker_thread'].is_alive(),'continuous_owned_workers_not_drained')
            b.require('queue_error_type' not in work and not work['queue_thread'].is_alive(),'continuous_queue_monitor_failed')
            for label in ('normal','overload','transition','recovery'):
                rows=[r for r in continuous['rows'] if r['phase']==label]
                mixed[label]={'business':stats(rows,'business'),'login':stats(rows,'login'),'WS':continuous['summary']['WS_opened'],'raw_files':save_rows(plan,label,rows)}
            requirement('normal_full_samples_and_zero_unexpected_denial',mixed['normal']['business']['successes']==10000 and mixed['normal']['login']['successes']==1000 and mixed['normal']['WS']==30)
            report['mixed']=mixed
            internal=[r for r in lab.admission_rows[internal_offset:] if r['actual_expensive_calls']==1 and r['entered']<work['start']+1000]
            report['normal_internal_hash_ms']=performance.summary([r['hash_ms'] for r in internal])
            report['normal_internal_queue_ms']=performance.summary([r['queue_ms'] for r in internal])
            report['normal_internal_rows_files']=save_rows(plan,'normal-auth-internal',internal)
            requirement('normal_hash_N1000',len(internal)>=1000)
            requirement('normal_hash_p95',bool(internal) and report['normal_internal_hash_ms']['p95']<=500)
            requirement('normal_hash_p99',bool(internal) and report['normal_internal_hash_ms']['p99']<=1000)
            requirement('normal_login_p95',mixed['normal']['login']['ms']['p95']<=1000)
            requirement('normal_login_p99',mixed['normal']['login']['ms']['p99']<=2000)
            requirement('business_p95_vs_nohash',mixed['normal']['business']['ms']['p95']<=mixed['baseline']['business']['ms']['p95']*1.2)
            overload=work['overload']
            full=[r for r in overload['rows'] if r['reason'] in ('ingress_full','queue_full')]
            rejected=[r for r in overload['rows'] if not r.get('actually_admitted',r['allowed'])]
            report['overload']={'full_queue_reject_ms':performance.summary([r['ms'] for r in full]),'rows_files':overload['raw_files'],'max_active':overload['max_active'],'max_queued':overload['max_queued'],'actual_hashes':sum(r['expensive_calls'] for r in overload['rows']),'rejected_expensive_calls':sum(r['expensive_calls'] for r in rejected),'client_timeouts':sum(not r['allowed'] and r.get('actually_admitted',False) for r in overload['rows'])}
            actual_windows=actual_hash_windows(overload['rows'],work['start']+1000,600)
            report['overload']['actual_hashes_per_10s_window']=actual_windows
            requirement('real_hash_overload_all_600s_covered',all(value>0 for value in actual_windows))
            report['overload']['by_reason']={reason:{'n':sum(r['reason']==reason for r in overload['rows']),'caller_ms':performance.summary([r['ms'] for r in overload['rows'] if r['reason']==reason])} for reason in sorted({r['reason'] for r in overload['rows']})}
            requirement('full_queue_tail_N',len(full)>=1000)
            requirement('full_queue_reject_p99_500ms',bool(full) and performance.quantile([r['ms'] for r in full],.99)<=500)
            check('R02_actual_rejected_expensive_zero',all(r['expensive_calls']==0 for r in rejected))
            check('R02_real_workers_hash_slots_2',overload['max_active']<=2 and overload['max_queued']<=8)
            check('R02_actual_queue_deadline_before_hash',all(r['queue_ms']<=1000 for r in overload['rows'] if r.get('actually_admitted')))
            report['recovery']=mixed['recovery']
            curve=queue_recovery(queue_samples,work['start']);report['recovery']['queue_curve']=curve
            first_stable=curve['first_10s_stable_window_end_seconds']
            requirement('queue_returns_to_baseline_in_60s',first_stable is not None and first_stable<=60 and curve['recovery_windows_meet_baseline'])
            requirement('continuous_2min_recovery',report['recovery']['business']['successes']==1200 and report['recovery']['login']['successes']==120 and report['recovery']['login']['ms']['p95']<=1000 and report['recovery']['business']['ms']['p95']<=mixed['baseline']['business']['ms']['p95']*1.2)
        finally:lab.close()
        stop.set();thread.join(5)
        b.require(not thread.is_alive() and 'resource_monitor_error' not in report and bool(samples),'resource_monitor_evidence_missing')
        report['resources']={**limits.verify(),**limits.resident_sample()};requirement('memory_25percent_headroom',max(report['resources']['SUT_peak_commit_bytes'],report['resources']['SUT_observed_peak_RSS_sum_bytes'])<=3*1024**3)
        report['result']='PASS' if not report['threshold_failures'] else 'FAIL'
        report['deployment_capacity_accepted']=False
        return report
    except BaseException as ex:
        report['result']='STOP';report['error_type']=type(ex).__name__;report['sqlstate']=getattr(ex,'sqlstate',None)
        raise
    finally:
        stop.set();thread.join(5)
        b.write(plan.run_root/'CAPACITY-RESOURCES.json',{'sample_files':save_rows(plan,'resources',samples),'sample_count':len(samples),'interval_seconds':1,'hard_limit':4*1024**3,'hard_memory_metric':'aggregate committed memory; RSS sum sampled separately'})
        b.write(plan.run_root/'CAPACITY-RESULTS.json',report)
