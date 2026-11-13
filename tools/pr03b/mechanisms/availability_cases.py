"""Seven ordinary HTTPS requests on one fresh isolated target; no load generator."""
import json,time
import common as b,transport

def smoke(lease,plan,check):
    # One ordinary overlap control, not a capacity/queue/load experiment.
    from concurrent.futures import ThreadPoolExecutor
    import threading
    pin=b._source_pin;barrier=threading.Barrier(2);before=pin.statistics()
    def verify(_):barrier.wait(5);return b.bundle()
    with ThreadPoolExecutor(max_workers=2) as readers:digests=list(readers.map(verify,range(2)))
    after=pin.statistics()
    check('ordinary_source_two_complete_fresh_verifications',digests==[pin.digest]*2 and after['completed']-before['completed']==2)
    check('ordinary_source_readers_returned',after['active']==0 and after['borrowed']==0)
    # Corrupt only a trusted test parameter, never any source or old input.
    original=pin.files['selftest.py'];pin.files['selftest.py']=(*original[:2],'0'*64)
    try:
        try:b.bundle()
        except b.Stop as error:check('ordinary_changed_expected_source_hash_denied',str(error)=='pinned_source_content_changed')
        else:check('ordinary_changed_expected_source_hash_denied',False)
    finally:pin.files['selftest.py']=original
    check('ordinary_source_after_parameter_control',b.bundle()==pin.digest)
    root=b.HOME/'availability-TLS-private';root.mkdir();lab=transport.Lab(lease,root,business=True);rows=[]
    def call(label,path,body=None,**options):
        start=time.monotonic()
        try:
            status,value,headers=transport.request(lab,path,body,**options)
            rows.append({'label':label,'status':status,'seconds':time.monotonic()-start})
            return status,value,headers
        except BaseException as error:
            rows.append({'label':label,'error_type':type(error).__name__,'seconds':time.monotonic()-start});raise
    try:
        # One synthetic owned document; no business migration or real data.
        with lease.connect('bootstrap') as c:
            c.execute('BEGIN READ WRITE')
            c.execute("INSERT INTO identity_control.lab_documents VALUES(2,990002,'S3-ORDINARY-SYNTHETIC-DOC')")
            c.execute('COMMIT')
        status,pre,headers=call('preauth','/lab/preauth');check('ordinary_preauth',status==200)
        status,value,headers=call('login','/lab/login',{'username':'synthetic-student','password':lab.password,'remember_me':False},csrf=pre['csrf'],headers={'Cookie':headers['Set-Cookie'].split(';',1)[0]})
        check('ordinary_complete_login',status==200 and value.get('state')=='authenticated')
        session=headers['Set-Cookie'].split(';',1)[0].split('=',1)[1]
        status,_,_=call('owned_business','/lab/business',session=session);check('ordinary_owned_business',status==200)
        lab.fault_sql_timeout=True
        try:status,_,_=call('fixed_PG_timeout','/lab/me',session=session)
        finally:lab.fault_sql_timeout=False
        check('fixed_PG_timeout_safe_response',status==401 and rows[-1]['seconds']<=5)
        status,_,_=call('after_fault_business','/lab/business',session=session);check('ordinary_business_recovers',status==200)
        status,pre,headers=call('wrong_password_context','/lab/preauth');check('ordinary_wrong_context',status==200)
        status,_,_=call('wrong_password','/lab/login',{'username':'synthetic-student','password':'fixed-synthetic-wrong','remember_me':False},csrf=pre['csrf'],headers={'Cookie':headers['Set-Cookie'].split(';',1)[0]})
        check('ordinary_wrong_password_denied',status==401)
    finally:
        b.write(plan.run_root/'R02-AVAILABILITY-REQUESTS.json',{'rows':rows,'pressure_test':False,'capacity_claim':False})
        lab.close()
    trace=lab.request_log.snapshot();fault=[r for r in trace['requests'] if r['route']=='/lab/me']
    check('original_PG_exception_preserved_before_HTTP_mapping',len(fault)==1 and fault[0]['first_exception']['sqlstate']=='57014' and fault[0]['first_exception']['stage']=='sql.body.session')
    check('request_correlation_complete',trace['total_requests']==7 and trace['discarded_requests']==0 and len({r['request_id'] for r in trace['requests']})==7)
    encoded=json.dumps(trace).encode()
    check('request_diagnostics_no_synthetic_secrets',not any(v.encode() in encoded for v in lab.markers if len(v)>=16) and session.encode() not in encoded)
    return {'requests':len(rows),'users':1,'concurrency':1,'stress_test':False,'capacity_or_enumeration_PASS':False,'first_fault_sqlstate':fault[0]['first_exception']['sqlstate'],'normal_and_after_fault_passed':True}
