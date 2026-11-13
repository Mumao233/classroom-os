"""Short diagnostic ladder before the unchanged full capacity acceptance."""
import threading,time
import common as b,schema as s,transport,performance,resource_group

def experiments(lease,plan,env,check):
    import guard_cost
    guard_cost.experiments(lease,plan,check,env)
    report={'purpose':'diagnostic only; N is not a p99 acceptance sample','steps':[{'seconds':10,'business_rps':1,'login_rps':0},{'seconds':10,'business_rps':3,'login_rps':.2},{'seconds':10,'business_rps':10,'login_rps':1}],'full_acceptance_sample_reduced':False,'arms':[]}
    b.write(plan.run_root/'R04-GRADED-PLAN.json',report)
    root=b.HOME/'graded-TLS-private';root.mkdir();lab=transport.Lab(lease,root,business=True)
    resources=[];stop=threading.Event()
    def sample():
        try:
            while not stop.wait(2):resources.append({'monotonic':time.monotonic(),**resource_group.CURRENT.resident_sample()})
        except BaseException as error:report['resource_error_type']=type(error).__name__
    sampler=threading.Thread(target=sample,daemon=True);sampler.start()
    try:
        lab.install_load_accounts();lab.ws_start();tokens=[];csrf=[]
        for actor in s.LOAD_ACTORS:
            token,row=lab.login(actor,password_version=1);tokens.append(token);csrf.append(row['csrf'])
        for baseline in (True,False):
            lab.no_hash_baseline=baseline
            label='R04-graded-'+('nohash' if baseline else 'hash')
            actual=performance.run_client(lease,plan,env,lab,'graded',seconds=30,sessions=tokens,csrf=csrf,label=label)
            groups={}
            for tier in ('graded_1rps','graded_3rps','graded_10rps'):
                for kind in ('business','login'):
                    rows=[v for v in actual['rows'] if v['phase']==tier and v['kind']==kind]
                    if not rows:continue
                    okay=[v for v in rows if v['status']==200 and (kind!='login' or v.get('authenticated') is True)]
                    groups[tier+'/'+kind]={'offered':len(rows),'successful':len(okay),'timeouts':sum(v['timeout'] for v in rows),'statuses':{str(code):sum(v['status']==code for v in rows) for code in {v['status'] for v in rows}},'all_ms':performance.summary([v['ms'] for v in rows]),'success_ms':performance.summary([v['ms'] for v in okay])}
            good=all(v['successful']==v['offered'] for v in groups.values()) and len(actual['rows'])==152 and actual['summary']['WS_opened']==30
            report['arms'].append({'nohash_baseline':baseline,'groups':groups,'client_seconds':actual['summary']['seconds'],'all_expected_requests_succeeded':good})
            b.write(plan.run_root/('R04-GRADED-'+('NOHASH' if baseline else 'HASH')+'.json'),report['arms'][-1])
            check('graded_diagnostic_no_unexpected_failure:'+str(baseline),good)
        return report
    finally:
        stop.set();sampler.join(5)
        b.write(plan.run_root/'R04-GRADED-RESOURCES.json',{'rows':resources,'interval_seconds':2,'monitor_error':report.get('resource_error_type')})
        b.write(plan.run_root/'R04-GRADED-RESULTS.json',report)
        lab.close()
