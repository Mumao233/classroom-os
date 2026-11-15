"""Fixed real PG failure budgets and complete NAT HTTPS control."""
import json,time
import common as b,schema as s,transport
def experiments(lease,plan,check):
    def budget(c,key,limit,window,inc):return c.execute('SELECT identity_api.lab3_fail_budget(%s,%s,%s,%s,%s)',(s.base.ticket('auth',2),key,limit,window,inc)).fetchone()[0]
    for key,limit,window in (('password-account',3,900),('TOTP-account',5,900),('recovery-code-account',5,900),('IP-failures',20,60)):
        with lease.connect('auth') as a,lease.connect('auth') as z:
            for i in range(limit):
                c=a if i%2==0 else z;c.execute('BEGIN READ WRITE');r=budget(c,key,limit,window,True);c.execute('COMMIT');check('failure_budget_increment:'+key+':'+str(i),r['allowed'] is True)
            for c in (a,z):
                c.execute('BEGIN READ WRITE');r=budget(c,key,limit,window,False);c.execute('COMMIT');check('failure_budget_cross_connection_reject:'+key,r['allowed'] is False and r['n']==limit)
        with lease.connect('bootstrap') as c:
            c.execute('BEGIN READ WRITE');c.execute('UPDATE identity_control.lab3_budgets SET window_start=clock_timestamp()-(%s*interval \'1 second\') WHERE key=%s',(window+1,key));c.execute('COMMIT')
        with lease.connect('auth') as c:
            c.execute('BEGIN READ WRITE');r=budget(c,key,limit,window,False);c.execute('COMMIT');check('failure_budget_expired_window_recovers:'+key,r['allowed'] is True and r['n']==0)
    with lease.connect('auth') as c:
        for i in range(60):
            c.execute('BEGIN READ WRITE');r=budget(c,'successful-login-IP',20,60,False);c.execute('COMMIT');check('T09_success_not_failure_count:'+str(i),r['allowed'] and r['n']==0)
    for key,limit in (('source-creation',90),('global-creation',120)):
        with lease.connect('auth') as c:
            c.execute('BEGIN READ WRITE')
            for i in range(limit):b.require(budget(c,key,limit,60,True)['allowed'],'preauth_budget_early_rejection')
            r=budget(c,key,limit,60,True);check('preauth_exact_SQL_budget:'+key,not r['allowed'] and r['n']==limit);c.execute('COMMIT')
    root=b.HOME/'NAT-TLS-private';root.mkdir();lab=transport.Lab(lease,root);lab.next_id=900000
    rows=[];start=time.monotonic()
    try:
        for i in range(60):
            wait=start+i-time.monotonic()
            if wait>0:time.sleep(wait)
            before=time.monotonic();code,pre,headers=transport.request(lab,'/lab/preauth');b.require(code==200,'NAT_preauth_control')
            code,result,_=transport.request(lab,'/lab/login',{'username':'synthetic-student','password':lab.password,'remember_me':False},csrf=pre['csrf'],headers={'Cookie':headers['Set-Cookie'].split(';',1)[0]})
            rows.append({'i':i,'start_seconds':round(before-start,3),'ms':round((time.monotonic()-before)*1000,3),'status':code});check('NAT_60_complete_logins_success:'+str(i),code==200 and result['state']=='authenticated')
        # This is a continuous instance. Counts are never reset during trial.
        creation=[]
        for _ in range(32):
            code,_,_=transport.request(lab,'/lab/preauth');creation.append(code)
        check('preauth_real_HTTP_response_mapping',all(x in (200,429) for x in creation))
        report={'rows':rows,'trial_seconds':round(time.monotonic()-start,3),'effective_logins_per_minute':round(3600/max(.001,rows[-1]['start_seconds']-rows[0]['start_seconds']+1),3),'source_creation_statuses':creation,'HTTP_source_exhaustion_observed':429 in creation,'successes_counted_as_failures':False,'namespace_created_once_before_trial':True}
        b.write(plan.run_root/'RATE-CONTROLS.json',report);return report
    finally:lab.close()
