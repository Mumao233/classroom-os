"""Actual PG barriers and fixed protocol experiments. No caller-selected SQL."""
from __future__ import annotations
import hashlib,json,secrets,threading,time
import sys,traceback
from datetime import datetime,timedelta,timezone
from pathlib import Path
import common as b
import schema as s
base=b.load('_s3_foundation_cases',b.BASE/'cases.py' if b.BASE.exists() else Path(__file__).resolve().parents[1]/'batch4/cases.py')
base.s=s.base
installation_controls=base.installation_controls
catalog=base.catalog
role_cases=base.role_cases
structural_sql=base.structural_sql
digest=base.digest
expect_error=base.expect_error
def state(c):
    return c.execute("SELECT md5(jsonb_build_array((SELECT jsonb_agg(to_jsonb(v)) FROM (SELECT * FROM identity_control.lab3_cases ORDER BY id)v),(SELECT jsonb_agg(to_jsonb(v)) FROM (SELECT * FROM identity_control.lab3_proofs ORDER BY id)v),(SELECT jsonb_agg(to_jsonb(v)) FROM (SELECT * FROM identity_control.lab3_ops ORDER BY id)v),(SELECT jsonb_agg(to_jsonb(v)) FROM (SELECT * FROM identity_control.lab_principals ORDER BY id)v),(SELECT jsonb_agg(to_jsonb(v)) FROM (SELECT * FROM identity_control.lab3_factors ORDER BY owner,generation)v))::text)").fetchone()[0]
def denied(c,sql,args,check,label,wanted='42501'):
    import psycopg
    c.execute('SAVEPOINT lab_denied');observed=None
    try:c.execute(sql,args)
    except psycopg.Error as ex:observed=ex.sqlstate
    c.execute('ROLLBACK TO SAVEPOINT lab_denied');c.execute('RELEASE SAVEPOINT lab_denied')
    check(label,observed==wanted,{'observed_sqlstate':observed})
def fixture(open_connection,cid,executor=3,target=1,ready=False):
    with open_connection('bootstrap') as c:
        c.execute('BEGIN READ WRITE')
        epoch=c.execute('SELECT epoch FROM identity_control.lab_principals WHERE id=%s',(target,)).fetchone()[0]
        c.execute('INSERT INTO identity_control.lab3_cases(id,executor,target,target_epoch,state,flow,receiver,expires,cap_hash) VALUES(%s,%s,%s,%s,%s,1,\'receiver-a\',clock_timestamp()+interval \'600 seconds\',\'cap-digest\')',(cid,executor,target,epoch,'pending_confirmed' if ready else 'open'))
        if ready:c.execute("INSERT INTO identity_control.lab3_factors VALUES(%s,%s,'pending',decode('010204','hex'))",(target,cid))
        c.execute('COMMIT')
def proof(open_connection,cid,pid,action='complete',age=0,challenge_delta=300,deadline_delta=120,executor=3,plan='plan-digest',flow=1,receiver='receiver-a'):
    # Trusted synthetic verifier setup, not an exposed proof-issuing API. Real
    # signature/peer/factor verification is exercised by the prototype adapter.
    with open_connection('bootstrap') as c:
        c.execute('BEGIN READ WRITE')
        c.execute("INSERT INTO identity_control.lab3_ops VALUES(%s,%s,%s,%s,'reserved',NULL)",(pid,cid,executor,action))
        c.execute("WITH n AS (SELECT clock_timestamp() AS now) INSERT INTO identity_control.lab3_proofs(id,op,case_id,executor,action,plan_hash,receiver,flow,verified_at,challenge_expiry,deadline,guard_version,manifest_hash) SELECT %s,%s,%s,%s,%s,%s,%s,%s,n.now-(%s*interval '1 second'),n.now+(%s*interval '1 second'),n.now+(%s*interval '1 second')-(%s*interval '1 second'),g.version,g.manifest_hash FROM n CROSS JOIN identity_control.lab_guard g WHERE g.id=1",
            (pid,pid,cid,executor,action,plan,receiver,flow,age,challenge_delta,deadline_delta,age))
        c.execute('COMMIT')
def write(c,pid,executor=3,plan='plan-digest',cap='cap-digest'):
    return c.execute('SELECT identity_api.lab3_write(%s,%s,%s,%s)',(s.base.ticket('recovery',executor),pid,plan,cap)).fetchone()[0]
def status(c,cid,op,executor=3,deadline=None):
    deadline=deadline or datetime.now(timezone.utc)+timedelta(seconds=120)
    return c.execute('SELECT identity_api.lab3_status(%s,%s,%s,%s)',(s.base.ticket('status',executor),cid,op,deadline)).fetchone()[0]
def worker(name,body):
    box={};done=threading.Event()
    def run():
        try:box['value']=body()
        except BaseException as ex:box['error_type']=type(ex).__name__;box['sqlstate']=getattr(ex,'sqlstate',None)
        finally:done.set()
    t=threading.Thread(name=name,target=run,daemon=True);t.start();return t,done,box
def pair_done(a,z,label,seconds=15):
    end=time.monotonic()+seconds
    okay=a[1].wait(seconds) and z[1].wait(max(.001,end-time.monotonic()))
    if not okay:
        frames=sys._current_frames();rows=[]
        for thread,done,box in (a,z):
            stack=traceback.extract_stack(frames[thread.ident]) if thread.ident in frames else []
            rows.append({'thread':thread.name,'done':done.is_set(),'error_type':box.get('error_type'),'sqlstate':box.get('sqlstate'),'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in stack]})
        b.write(b.HOME/'evidence'/('WORKER-STOP-'+secrets.token_hex(8)+'.json'),{'label':label,'workers':rows})
    b.require(okay,label)
def wait_blocked(observer,pid,deadline=4):
    until=time.monotonic()+deadline
    while time.monotonic()<until:
        row=observer.execute('SELECT pg_blocking_pids(%s)',(pid,)).fetchone()
        if row and row[0]:return True
        time.sleep(.02)
    return False
def protocols(lease,check):
    open_connection=lease.connect
    # Scope mutations and stale proof: no nonce/cap/epoch effects on denial.
    for i,(age,flow,receiver,plan) in enumerate(((240,1,'receiver-a','plan-digest'),(0,2,'receiver-a','plan-digest'),(0,1,'receiver-b','plan-digest'),(0,1,'receiver-a','other-plan'))):
        cid=1000+i;fixture(open_connection,cid,ready=True);proof(open_connection,cid,cid,age=age,flow=flow,receiver=receiver,plan=plan)
        with open_connection('bootstrap') as observer:before=state(observer)
        with open_connection('recovery') as c:
            c.execute('BEGIN READ WRITE');denied(c,'SELECT identity_api.lab3_write(%s,%s,%s,%s)',(s.base.ticket('recovery',3),cid,'plan-digest','cap-digest'),check,'D02_scope_stale_denied:'+str(i));c.execute('ROLLBACK')
        with open_connection('bootstrap') as observer:check('D02_denied_zero_change:'+str(i),state(observer)==before)
    # Real same-cap completion race: 20 independent cases, two fresh proofs.
    for repeat in range(20):
        cid=1100+repeat;fixture(open_connection,cid,ready=True);proof(open_connection,cid,cid*10);proof(open_connection,cid,cid*10+1)
        gate=threading.Barrier(2)
        def complete(pid):
            with open_connection('recovery') as c:
                c.execute('BEGIN READ WRITE');gate.wait(timeout=10)
                try:r=write(c,pid);c.execute('COMMIT');return r
                except BaseException:c.execute('ROLLBACK');raise
        a=worker('complete-a',lambda:complete(cid*10));z=worker('complete-b',lambda:complete(cid*10+1))
        b.require(a[1].wait(15) and z[1].wait(15),'completion_race_watchdog')
        check('D02_one_complete:'+str(repeat),sum('value' in x[2] for x in (a,z))==1 and sum(x[2].get('sqlstate')=='42501' for x in (a,z))==1)
        with open_connection('bootstrap') as c:
            row=c.execute('SELECT count(*) FROM identity_control.lab3_audit WHERE op IN(%s,%s)',(cid*10,cid*10+1)).fetchone()[0]
            check('D02_one_audit:'+str(repeat),row==1)
        with open_connection('status') as c:
            c.execute('BEGIN READ WRITE');winner=cid*10 if 'value' in a[2] else cid*10+1
            check('D03_consumed_cap_epoch_status:'+str(repeat),status(c,cid,winner)['result']=='committed')
            denied(c,'SELECT identity_api.lab3_status(%s,%s,%s,%s)',(s.base.ticket('status',4),cid,winner,datetime.now(timezone.utc)+timedelta(seconds=120)),check,'D03_other_admin_denied:'+str(repeat))
            c.execute('ROLLBACK')
    # Fresh complete remains possible after old issue approval is spent/stale.
    cid=1300;fixture(open_connection,cid);proof(open_connection,cid,13000,action='issue')
    with open_connection('recovery') as c:c.execute('BEGIN READ WRITE');check('D02_issue_control',write(c,13000)['committed']);c.execute('COMMIT')
    with open_connection('bootstrap') as c:
        c.execute('BEGIN READ WRITE')
        # One volatile clock sample: independent calls can drift by a few
        # microseconds and violate the unchanged <=120-second CHECK.
        c.execute("WITH n AS MATERIALIZED (SELECT clock_timestamp() AS now) UPDATE identity_control.lab3_proofs SET verified_at=n.now-interval '240 seconds',deadline=n.now-interval '120 seconds' FROM n WHERE id=13000")
        check('D02_aged_issue_exact_120s_and_still_consumed',c.execute('SELECT extract(epoch FROM deadline-verified_at),used FROM identity_control.lab3_proofs WHERE id=13000').fetchone()==(120,True))
        denied(c,"UPDATE identity_control.lab3_proofs SET deadline=verified_at+interval '120.000001 seconds' WHERE id=13000",(),check,'D02_over_120s_CHECK_still_rejects',wanted='23514')
        c.execute("UPDATE identity_control.lab3_cases SET state='pending_confirmed' WHERE id=1300")
        c.execute("INSERT INTO identity_control.lab3_factors VALUES(1,1300,'pending',decode('01','hex'))");c.execute('COMMIT')
    proof(open_connection,cid,13001)
    with open_connection('recovery') as c:c.execute('BEGIN READ WRITE');check('D02_fresh_complete_old_issue_used',write(c,13001)['committed']);c.execute('COMMIT')
    with open_connection('status') as c:
        c.execute('BEGIN READ WRITE');first=status(c,cid,13001)
        for _ in range(20):check('D03_repeat_status_no_secret',status(c,cid,13001)==first and set(first)=={'result','stage','audit_id'})
        check('D03_missing_unknown',status(c,999999,999999)=={'result':'unknown','stage':None,'audit_id':None});c.execute('ROLLBACK')
    # New op on completed case is never authorized by a not_committed read.
    proof(open_connection,cid,13002)
    with open_connection('recovery') as c:
        c.execute('BEGIN READ WRITE');denied(c,'SELECT identity_api.lab3_write(%s,%s,%s,%s)',(s.base.ticket('recovery',3),13002,'plan-digest','cap-digest'),check,'D03_no_reexecute_completed');c.execute('ROLLBACK')
def real_expiry(lease,check):
    # One actual >=121 second lock wait, not an accelerated/global clock seam.
    cid=1400;fixture(lease.connect,cid,ready=True);proof(lease.connect,cid,14000)
    ready=threading.Event();pid=[];started=time.monotonic()
    with lease.connect('bootstrap') as blocker,lease.connect('bootstrap') as observer:
        blocker.execute('BEGIN READ WRITE');blocker.execute("SET LOCAL idle_in_transaction_session_timeout='240s'")
        blocker.execute('SELECT id FROM identity_control.lab_principals WHERE id=3 FOR UPDATE')
        def attempt():
            with lease.connect('recovery') as c:
                c.execute('BEGIN READ WRITE');c.execute("SET LOCAL lock_timeout='180s'; SET LOCAL statement_timeout='210s'")
                pid.append(c.c.info.backend_pid);ready.set()
                try:write(c,14000);c.execute('COMMIT');return 'unexpected_success'
                except BaseException:c.execute('ROLLBACK');raise
        thread,done,box=worker('D02-real-lock-expiry',attempt)
        b.require(ready.wait(10),'expiry_worker_not_ready');check('D02_actual_lock_wait_observed',wait_blocked(observer,pid[0],10))
        status_ready=threading.Event();status_pid=[];status_deadline=datetime.now(timezone.utc)+timedelta(seconds=120)
        def read_status():
            with lease.connect('status') as c:
                c.execute('BEGIN READ WRITE');c.execute("SET LOCAL lock_timeout='180s'; SET LOCAL statement_timeout='210s'")
                status_pid.append(c.c.info.backend_pid);status_ready.set()
                try:r=status(c,cid,14000,deadline=status_deadline);c.execute('ROLLBACK');return r
                except BaseException:c.execute('ROLLBACK');raise
        st,sd,sb=worker('D03-real-status-expiry',read_status)
        b.require(status_ready.wait(10),'status_expiry_worker_not_ready');check('D03_actual_shared_lock_wait_observed',wait_blocked(observer,status_pid[0],10))
        while datetime.now(timezone.utc)<status_deadline+timedelta(seconds=2):time.sleep(.2)
        blocker.execute('ROLLBACK');b.require(done.wait(20),'expiry_worker_not_done')
        b.require(sd.wait(20),'status_expiry_worker_not_done');check('D03_real_status_lock_then_expired',sb.get('sqlstate')=='42501')
        check('D02_real_121s_lock_then_expired',box.get('sqlstate')=='42501',{'elapsed_seconds':round(time.monotonic()-started,3),'blocking_pid_observed':True})
        check('D02_long_wait_zero_proof_consumption',observer.execute('SELECT used FROM identity_control.lab3_proofs WHERE id=14000').fetchone()[0] is False)
def secondary_lock_expiry(lease,check):
    """Fresh proof must expire while waiting for factor AND group locks."""
    cid=1410;fixture(lease.connect,cid,ready=True);proof(lease.connect,cid,14100)
    ready=threading.Event();pids=[]
    with lease.connect('bootstrap') as factor,lease.connect('bootstrap') as group,lease.connect('bootstrap') as observer:
        for blocker in (factor,group):
            blocker.execute('BEGIN READ WRITE');blocker.execute("SET LOCAL idle_in_transaction_session_timeout='240s'")
        factor.execute('SELECT owner FROM identity_control.lab3_factors WHERE owner=1 ORDER BY generation FOR UPDATE')
        group.execute('SELECT owner FROM identity_control.lab3_groups WHERE owner=1 ORDER BY generation FOR UPDATE')
        before=state(observer);began=[]
        def attempt():
            with lease.connect('recovery') as c:
                c.execute('BEGIN READ WRITE');c.execute("SET LOCAL lock_timeout='180s';SET LOCAL statement_timeout='210s'")
                pids.append(c.c.info.backend_pid);began.append(time.monotonic());ready.set()
                try:r=write(c,14100);c.execute('COMMIT');return r
                except BaseException:c.execute('ROLLBACK');raise
        thread,done,box=worker('D02-factor-group-expiry',attempt)
        b.require(ready.wait(10),'factor_expiry_not_ready')
        def blocks(pid):return pid in observer.execute('SELECT pg_blocking_pids(%s)',(pids[0],)).fetchone()[0]
        b.require(wait_blocked(observer,pids[0],10) and blocks(factor.c.info.backend_pid),'factor_lock_not_observed')
        check('D02_real_factor_lock_observed',True)
        while time.monotonic()-began[0]<122:time.sleep(.2)
        factor_wait=time.monotonic()-began[0];factor.execute('ROLLBACK')
        b.require(wait_blocked(observer,pids[0],10) and blocks(group.c.info.backend_pid),'group_lock_not_observed')
        check('D02_real_group_lock_observed_after_factor',True)
        group.execute('ROLLBACK');b.require(done.wait(20),'factor_group_expiry_not_done')
        check('D02_clock_after_all_factor_group_locks',box.get('sqlstate')=='42501',{'factor_wait_seconds':round(factor_wait,3),'factor_and_group_blockers_observed':True})
        check('D02_factor_group_wait_zero_recovery_effect',state(observer)==before)

def qualification_after_locks(lease,check):
    """Qualification expiry must also be fresh at the final lock boundary."""
    for mode,cid,oid in (('complete',1420,14200),('status',1421,14210)):
        fixture(lease.connect,cid,ready=True);proof(lease.connect,cid,oid)
        ready=threading.Event();pids=[]
        with lease.connect('bootstrap') as blocker,lease.connect('bootstrap') as observer:
            old=observer.execute('SELECT expires FROM identity_control.lab3_qualifications WHERE actor=3').fetchone()[0]
            blocker.execute('BEGIN READ WRITE');blocker.execute("SET LOCAL idle_in_transaction_session_timeout='240s'")
            if mode=='complete':blocker.execute('SELECT owner FROM identity_control.lab3_factors WHERE owner=1 ORDER BY generation FOR UPDATE')
            else:blocker.execute('SELECT id FROM identity_control.lab3_cases WHERE id=%s FOR UPDATE',(cid,))
            observer.execute('BEGIN READ WRITE')
            until=observer.execute("UPDATE identity_control.lab3_qualifications SET expires=clock_timestamp()+interval '5 seconds' WHERE actor=3 RETURNING expires").fetchone()[0]
            observer.execute('COMMIT');before=state(observer)
            def attempt():
                service='recovery' if mode=='complete' else 'status'
                with lease.connect(service) as c:
                    c.execute('BEGIN READ WRITE');c.execute("SET LOCAL lock_timeout='180s';SET LOCAL statement_timeout='210s'")
                    pids.append(c.c.info.backend_pid);ready.set()
                    try:
                        value=write(c,oid) if mode=='complete' else status(c,cid,oid)
                        c.execute('ROLLBACK');return value
                    except BaseException:c.execute('ROLLBACK');raise
            thread,done,box=worker('qualification-lock-expiry-'+mode,attempt)
            try:
                b.require(ready.wait(10),'qualification_worker_not_ready')
                check('qualification_later_lock_observed:'+mode,wait_blocked(observer,pids[0],10))
                while observer.execute('SELECT clock_timestamp()').fetchone()[0]<=until+timedelta(milliseconds=200):time.sleep(.1)
                check('qualification_expired_while_write_proof_still_fresh:'+mode,observer.execute('SELECT clock_timestamp()<deadline FROM identity_control.lab3_proofs WHERE id=%s',(oid,)).fetchone()[0])
                blocker.execute('ROLLBACK');b.require(done.wait(20),'qualification_worker_not_done')
                check('qualification_rechecked_after_all_locks:'+mode,box.get('sqlstate')=='42501')
                check('qualification_expiry_zero_recovery_effect:'+mode,state(observer)==before)
            finally:
                blocker.execute('ROLLBACK')
                b.require(done.wait(20),'qualification_cleanup_worker_active')
                observer.execute('BEGIN READ WRITE');observer.execute('UPDATE identity_control.lab3_qualifications SET expires=%s WHERE actor=3',(old,));observer.execute('COMMIT')

def notification_locks(lease,check):
    for mode in ('producer','claim','delivery','retry','ack','cleanup'):
        for repeat in range(20):
            cid=1500+repeat+20*('producer','claim','delivery','retry','ack','cleanup').index(mode)
            fixture(lease.connect,cid,ready=True);proof(lease.connect,cid,cid*10)
            # Explicit old L(counter)->Outbox FK(user KEY SHARE) wait, while S
            # already holds user UPDATE. S must never request that counter.
            with lease.connect('bootstrap') as sec,lease.connect('bootstrap') as obs:
                sec.execute('BEGIN READ WRITE');sec.execute("SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='10s'")
                sec.execute('SELECT id FROM identity_control.lab_principals WHERE id=1 FOR UPDATE')
                has_counter=threading.Event();ids=[]
                def local_reconcile():
                    with lease.connect('bootstrap') as local:
                        local.execute('BEGIN READ WRITE');local.execute("SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='10s'")
                        local.execute('SELECT owner FROM identity_control.lab3_business_counter WHERE owner=1 FOR UPDATE')
                        ids.append(local.c.info.backend_pid);has_counter.set()
                        local.execute('INSERT INTO identity_control.lab3_business_outbox VALUES(%s,1,\'local-only\')',(cid,));local.execute('COMMIT');return True
                t,done,box=worker('local-counter-FK',local_reconcile)
                b.require(has_counter.wait(10),'counter_worker');check('N01_FK_wait:'+mode+':'+str(repeat),wait_blocked(obs,ids[0]))
                # Match real producer: typed FK parent locks are already held.
                sec.execute('INSERT INTO identity_control.lab3_notices(id,subject,op) VALUES(%s,1,%s)',(cid*10,cid*10));sec.execute('COMMIT')
                b.require(done.wait(12),'notification_lock_watchdog');check('N01_no_counter_cycle:'+mode+':'+str(repeat),box.get('value') is True)
            with lease.connect('notice') as c:
                c.execute('BEGIN READ WRITE');check('N01_claim:'+mode+':'+str(repeat),c.execute('SELECT identity_api.lab3_notice(%s,%s,\'claim\',7)',(s.base.ticket('notice',5),cid*10)).fetchone()[0]);c.execute('COMMIT')
            # Delivery happens AFTER the claim transaction released notice.
            with lease.connect('bootstrap') as c:
                c.execute('BEGIN READ ONLY');check('N01_delivery_current:'+mode+':'+str(repeat),c.execute('SELECT active FROM identity_control.lab_principals WHERE id=1').fetchone()[0]);c.execute('ROLLBACK')
            with lease.connect('notice') as c:
                c.execute('BEGIN READ WRITE')
                if mode=='retry':
                    check('N01_retry_releases',c.execute('SELECT identity_api.lab3_notice(%s,%s,\'retry\',7)',(s.base.ticket('notice',5),cid*10)).fetchone()[0])
                    c.execute('SELECT identity_api.lab3_notice(%s,%s,\'claim\',8)',(s.base.ticket('notice',5),cid*10));token=8
                else:token=7
                check('N01_ack:'+mode+':'+str(repeat),c.execute('SELECT identity_api.lab3_notice(%s,%s,\'ack\',%s)',(s.base.ticket('notice',5),cid*10,token)).fetchone()[0])
                check('N01_cleanup:'+mode+':'+str(repeat),c.execute('SELECT identity_api.lab3_notice(%s,%s,\'cleanup\',0)',(s.base.ticket('notice',5),cid*10)).fetchone()[0]);c.execute('COMMIT')
    with lease.connect('bootstrap') as c:
        check('N01_task_evidence_and_provider_unchanged',c.execute('SELECT raw,receipt,outbox,fence,unknown,may_have_charged,provider_calls FROM identity_control.lab3_task_evidence').fetchone()==('S3-RAW','S3-RECEIPT','S3-OUTBOX',7,True,True,0))

def status_and_faults(lease,check):
    cid=1800;fixture(lease.connect,cid,ready=True);proof(lease.connect,cid,18000)
    with lease.connect('bootstrap') as c:before=state(c)
    with lease.connect('status') as c:
        c.execute('BEGIN READ WRITE');check('D03_reserved_pending_has_locked_not_committed',status(c,cid,18000)=={'result':'not_committed','stage':'pending_confirmed','audit_id':None});c.execute('ROLLBACK')
    with lease.connect('bootstrap') as c:check('D03_not_committed_zero_recovery_write',state(c)==before)
    with lease.connect('bootstrap') as c:
        c.execute('BEGIN READ WRITE');c.execute('UPDATE identity_control.lab3_cases SET flow=2 WHERE id=1800');c.execute('COMMIT')
    with lease.connect('status') as c:
        c.execute('BEGIN READ WRITE');check('D03_old_op_new_flow_unknown',status(c,cid,18000)['result']=='unknown')
        denied(c,'SELECT identity_api.lab3_status(%s,%s,%s,%s)',(s.base.ticket('status',3),cid,18000,datetime.now(timezone.utc)+timedelta(seconds=300)),check,'D03_query_deadline_cannot_extend_120s');c.execute('ROLLBACK')
    with lease.connect('bootstrap') as c:
        c.execute('BEGIN READ WRITE');c.execute('UPDATE identity_control.lab3_cases SET flow=1 WHERE id=1800');c.execute('COMMIT')
    # These are current verifier projections, not caller-provided status IDs.
    for field,value in (('session_current',False),('device_current',False),('mfa_current',False),('epoch',-1),('expires',datetime.now(timezone.utc)-timedelta(seconds=1))):
        with lease.connect('bootstrap') as c:
            c.execute('BEGIN READ WRITE');old=c.execute('SELECT '+field+' FROM identity_control.lab3_qualifications WHERE actor=3').fetchone()[0]
            c.execute('UPDATE identity_control.lab3_qualifications SET '+field+'=%s WHERE actor=3',(value,));c.execute('COMMIT')
        with lease.connect('status') as c:
            c.execute('BEGIN READ WRITE');denied(c,'SELECT identity_api.lab3_status(%s,%s,%s,%s)',(s.base.ticket('status',3),cid,18000,datetime.now(timezone.utc)+timedelta(seconds=120)),check,'D03_current_qualification:'+field);c.execute('ROLLBACK')
        with lease.connect('bootstrap') as c:
            c.execute('BEGIN READ WRITE');c.execute('UPDATE identity_control.lab3_qualifications SET '+field+'=%s WHERE actor=3',(old,));c.execute('COMMIT')
    for order in ('revoke_first','status_first'):
        for repeat in range(20):
            if order=='status_first':
                with lease.connect('status') as c,lease.connect('bootstrap') as observer:
                    c.execute('BEGIN READ WRITE');r=status(c,cid,18000);pid=[];ready=threading.Event()
                    def revoke():
                        with lease.connect('bootstrap') as m:
                            m.execute('BEGIN READ WRITE');m.execute("SET LOCAL lock_timeout='5s';SET LOCAL statement_timeout='10s'")
                            pid.append(m.c.info.backend_pid);ready.set();m.execute('UPDATE identity_control.lab3_permissions SET enabled=false WHERE actor=3');m.execute('COMMIT');return True
                    t,done,box=worker('revoke-after-status',revoke);b.require(ready.wait(10),'status_revoke_ready')
                    check('D03_revoke_waits_for_shared_status:'+str(repeat),wait_blocked(observer,pid[0]));c.execute('COMMIT')
                    b.require(done.wait(12),'status_revoke_watchdog');check('D03_status_linearizes_before_revocation:'+str(repeat),r['result']=='not_committed' and box.get('value') is True)
            else:
                with lease.connect('bootstrap') as c:c.execute('BEGIN READ WRITE');c.execute('UPDATE identity_control.lab3_permissions SET enabled=false WHERE actor=3');c.execute('COMMIT')
            with lease.connect('status') as c:
                c.execute('BEGIN READ WRITE');denied(c,'SELECT identity_api.lab3_status(%s,%s,%s,%s)',(s.base.ticket('status',3),cid,18000,datetime.now(timezone.utc)+timedelta(seconds=120)),check,'D03_current_revocation_denied:'+order+':'+str(repeat));c.execute('ROLLBACK')
            with lease.connect('bootstrap') as c:c.execute('BEGIN READ WRITE');c.execute('UPDATE identity_control.lab3_permissions SET enabled=true WHERE actor=3');c.execute('COMMIT')
    # Independent fault scenario gets its own fresh proof. The preceding 40
    # revocation schedules can outlive 120s; never renew their old proof.
    cid=1810;fault_op=18100;fixture(lease.connect,cid,ready=True);proof(lease.connect,cid,fault_op)
    # Actual audit insert failure rolls back proof/cap/factors/epoch/op together.
    with lease.connect('bootstrap') as c:
        c.execute('BEGIN READ WRITE');c.execute("CREATE FUNCTION identity_api.lab3_fail_audit() RETURNS trigger LANGUAGE plpgsql AS $lab$ BEGIN IF NEW.op=18100 THEN RAISE EXCEPTION 'synthetic audit unavailable' USING ERRCODE='23514';END IF;RETURN NEW;END;$lab$;REVOKE ALL ON FUNCTION identity_api.lab3_fail_audit() FROM PUBLIC;CREATE TRIGGER lab3_fault BEFORE INSERT ON identity_control.lab3_audit FOR EACH ROW EXECUTE FUNCTION identity_api.lab3_fail_audit()",prepare=False);c.execute('COMMIT');before=state(c)
    with lease.connect('recovery') as c:
        c.execute('BEGIN READ WRITE');denied(c,'SELECT identity_api.lab3_write(%s,%s,%s,%s)',(s.base.ticket('recovery',3),fault_op,'plan-digest','cap-digest'),check,'audit_failure_exact_SQLSTATE','23514');c.execute('ROLLBACK')
    with lease.connect('bootstrap') as c:
        check('audit_failure_no_partial_recovery',state(c)==before);c.execute('BEGIN READ WRITE');c.execute('DROP TRIGGER lab3_fault ON identity_control.lab3_audit;DROP FUNCTION identity_api.lab3_fail_audit()',prepare=False);c.execute('COMMIT')
    # Send COMMIT, flush it, then close without receiving its result. No retry.
    with lease.connect('recovery') as c:
        c.execute('BEGIN READ WRITE');write(c,fault_op)
        c.c.pgconn.send_query(b'COMMIT');c.c.pgconn.flush();c.c.close()
    with lease.connect('status') as c:
        c.execute('BEGIN READ WRITE');r=status(c,cid,fault_op);c.execute('ROLLBACK')
        check('D03_lost_COMMIT_response_resolved_by_independent_status',r['result'] in ('committed','not_committed'),{'observed_result':r['result'],'write_automatically_retried':False})
    # Real ordinary lock and statement timeout; no increased production budget.
    with lease.connect('bootstrap') as blocker:
        blocker.execute('BEGIN READ WRITE');blocker.execute('SELECT id FROM identity_control.lab_principals WHERE id=1 FOR UPDATE')
        with lease.connect('bootstrap') as c:
            c.execute('BEGIN READ WRITE');start=time.monotonic();denied(c,'SELECT id FROM identity_control.lab_principals WHERE id=1 FOR UPDATE',(),check,'ordinary_lock_timeout','55P03');check('ordinary_lock_bounded_5s',time.monotonic()-start<5);c.execute('ROLLBACK')
        blocker.execute('ROLLBACK')
    with lease.connect('bootstrap') as c:
        c.execute('BEGIN READ WRITE');start=time.monotonic();denied(c,'SELECT pg_sleep(4)',(),check,'ordinary_statement_timeout','57014');check('ordinary_statement_bounded_5s',time.monotonic()-start<5);c.execute('ROLLBACK')
    # Expired status/unknown identifiers never return not_committed by absence.
    with lease.connect('status') as c:
        c.execute('BEGIN READ WRITE');check('D03_absence_unknown',status(c,999999,999998)['result']=='unknown')
        denied(c,'SELECT identity_api.lab3_status(%s,%s,%s,%s)',(s.base.ticket('status',3),cid,fault_op,datetime.now(timezone.utc)-timedelta(seconds=1)),check,'D03_expired_ephemeral_proof');c.execute('ROLLBACK')
def session_primitives(lease,check):
    # Two independent connections consume the same TOTP step at most once.
    for repeat in range(20):
        barrier=threading.Barrier(2);step=100000+repeat
        def consume():
            with lease.connect('session') as c:
                c.execute('BEGIN READ WRITE');barrier.wait(timeout=10);v=c.execute('SELECT identity_api.lab3_step(%s,%s)',(s.base.ticket('session',2),step)).fetchone()[0];c.execute('COMMIT');return v
        a=worker('totp-a',consume);z=worker('totp-b',consume);b.require(a[1].wait(15) and z[1].wait(15),'TOTP_race_watchdog')
        check('TOTP_one_time_step:'+str(repeat),[a[2].get('value'),z[2].get('value')].count(True)==1 and [a[2].get('value'),z[2].get('value')].count(False)==1)
    for actor,idle,absolute in ((1,14*86400,30*86400),(3,900,28800)):
        sid=800000+actor
        old_hash='session-digest:'+str(actor);new_hash='new-digest:'+str(actor)
        with lease.connect('session') as c:
            c.execute('BEGIN READ WRITE');c.execute('SELECT identity_api.lab3_session(%s,\'establish\',%s,%s,\'\')',(s.base.ticket('session',actor),sid,old_hash));c.execute('COMMIT')
        with lease.connect('bootstrap') as c:
            row=c.execute('SELECT extract(epoch FROM absolute_deadline-idle_deadline) FROM identity_control.lab3_sessions WHERE id=%s',(sid,)).fetchone()[0]
            check('session_role_deadline_difference:'+str(actor),float(row)==absolute-idle)
        with lease.connect('session') as c:
            c.execute('BEGIN READ WRITE');c.execute('SELECT identity_api.lab3_session(%s,\'rotate\',%s,%s,%s)',(s.base.ticket('session',actor),sid,new_hash,old_hash));c.execute('COMMIT')
            c.execute('BEGIN READ WRITE');denied(c,'SELECT identity_api.lab3_session(%s,\'validate\',%s,\'\',%s)',(s.base.ticket('session',actor),sid,old_hash),check,'session_fixation_old_token_denied:'+str(actor));c.execute('ROLLBACK')
        with lease.connect('bootstrap') as c:
            c.execute('BEGIN READ WRITE');c.execute('UPDATE identity_control.lab3_sessions SET idle_deadline=clock_timestamp()-interval \'1 microsecond\' WHERE id=%s',(sid,));c.execute('COMMIT')
        with lease.connect('session') as c:
            c.execute('BEGIN READ WRITE');denied(c,'SELECT identity_api.lab3_session(%s,\'touch\',%s,\'\',%s)',(s.base.ticket('session',actor),sid,new_hash),check,'session_touch_no_expiry_resurrection:'+str(actor));c.execute('ROLLBACK')

def authority_control(lease,check):
    """Synthetic project-user authority; never a real identity or root install."""
    import protocol,crypto,windows
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    d0=protocol.CURRENT_FIXTURE;b.require(d0 is not None,'synthetic_D0_fixture_required')
    cid=2900;oid=29000;fixture(lease.connect,cid,executor=5,target=3,ready=True)
    seed=secrets.token_bytes(20);kek=AESGCM.generate_key(bit_length=256);iv=secrets.token_bytes(12);cipher=AESGCM(kek).encrypt(iv,seed,b'admin-synthetic-factor')
    with lease.connect('bootstrap') as c:
        c.execute('BEGIN READ WRITE');c.execute('UPDATE identity_control.lab3_factors SET encrypted=%s WHERE owner=3 AND generation=%s',(iv+cipher,cid));c.execute('COMMIT')
        epoch=c.execute('SELECT epoch FROM identity_control.lab_principals WHERE id=3').fetchone()[0]
        group_count=c.execute('SELECT count(*) FROM identity_control.lab3_groups WHERE owner=3').fetchone()[0]
        qualification=c.execute('SELECT expires FROM identity_control.lab3_qualifications WHERE actor=5').fetchone()[0]
    plan={'deployment':d0.manifest['deployment'],'executor':d0.manifest['authority'],'case':cid,'op':oid,'target':3,'role':'admin','epoch':epoch,'action':'complete','flow':1,'receiver':'receiver-a','evidence':'synthetic-independent-user-check','confirmation':hashlib.sha256(cipher).hexdigest()}
    digest=hashlib.sha256(crypto.jcs(plan)).hexdigest()
    def db_now():
        with lease.connect('bootstrap') as c:return c.execute('SELECT clock_timestamp()').fetchone()[0]
    with d0.gate.hold():
        with lease.connect('bootstrap') as c:pg=c.execute('SELECT version,manifest_hash FROM identity_control.lab_guard WHERE id=1').fetchone()
        d0.ready(pg);now=db_now();challenge={'plan':plan,'nonce':crypto.b64(secrets.token_bytes(32)),'expires':(now+timedelta(seconds=300)).isoformat(),'qualification':qualification.isoformat()}
        verifier=protocol.Verifier(d0.recover.public_key(),d0.approval['os_subject']);sig=d0.recover.sign(crypto.signed('classroom.identity.recovery-proof.v1',challenge))
        verified,deadline=verifier.verify(challenge,sig,windows.sid_string(),plan,db_now)
        proof(lease.connect,cid,oid,executor=5,plan=digest)
        with lease.connect('bootstrap') as c:
            c.execute('BEGIN READ WRITE');c.execute('UPDATE identity_control.lab3_proofs SET verified_at=%s,deadline=%s,challenge_expiry=%s WHERE id=%s',(verified,deadline,datetime.fromisoformat(challenge['expires']),oid));c.execute('COMMIT');before=state(c)
        for actor in (3,4):
            with lease.connect('recovery') as c:
                c.execute('BEGIN READ WRITE');denied(c,'SELECT identity_api.lab3_write(%s,%s,%s,%s)',(s.base.ticket('recovery',actor),oid,digest,'cap-digest'),check,'A32_other_admin_cannot_complete:'+str(actor));c.execute('ROLLBACK')
        with lease.connect('bootstrap') as c:check('A32_other_admin_zero_effect',state(c)==before)
        with lease.connect('recovery') as c:
            c.execute('BEGIN READ WRITE');r=write(c,oid,executor=5,plan=digest);c.execute('COMMIT');check('A32_synthetic_OS_key_user_manual_complete',r['committed'] and r['new_recovery_codes_issued'] is False)
    with lease.connect('status') as c:
        c.execute('BEGIN READ WRITE');r=status(c,cid,oid,executor=5);c.execute('ROLLBACK');check('A32_authority_status_after_target_epoch',r=={'result':'committed','stage':'completed','audit_id':oid})
    with lease.connect('bootstrap') as c:
        check('A32_final_exact_epoch_old_codes_only',c.execute('SELECT epoch FROM identity_control.lab_principals WHERE id=3').fetchone()[0]==epoch+1 and c.execute('SELECT count(*),count(*) FILTER(WHERE NOT revoked) FROM identity_control.lab3_groups WHERE owner=3').fetchone()==(group_count,0))
        stored=bytes(c.execute('SELECT encrypted FROM identity_control.lab3_factors WHERE owner=3 AND generation=%s AND state=\'active\'',(cid,)).fetchone()[0]);check('A32_new_factor_cipher_intact',AESGCM(kek).decrypt(stored[:12],stored[12:],b'admin-synthetic-factor')==seed)
    return {'synthetic_only':True,'real_project_user_identity_used':False,'manual_complete_executor':5,'target_role':'admin'}
