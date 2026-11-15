"""Fixed expired-queue counterexample; never expires an executing reservation."""
import hashlib,os,time
from datetime import timedelta
import common as b,schema as s
CLEANUP="DELETE FROM identity_control.lab3_hash_jobs WHERE state='queued' AND deadline<=n;"
OLD='lab3_hash_admission_expected_old'
def experiments(lease,plan,check):
    import native_process
    wid=os.getpid();ct=str(native_process.observe_process_details(wid).identity.creation_token)
    ticket=s.base.ticket('auth',2);rows=[]
    sql=s.extension(plan.run_id);start=sql.index('CREATE FUNCTION identity_api.lab3_hash_admission(');end=sql.index('$lab$;',start)+len('$lab$;')
    original=sql[start:end];b.require(original.count(CLEANUP)==1,'queue_cleanup_control_definition')
    old=original.replace(CLEANUP,'',1).replace('CREATE FUNCTION identity_api.lab3_hash_admission(','CREATE FUNCTION identity_api.'+OLD+'(',1)
    signature='identity_api.'+OLD+'(text,text,bigint,integer,text,timestamptz)'
    ddl=old+'\nALTER FUNCTION '+signature+' OWNER TO '+s.base.ident(s.OWNERS['auth'])+';REVOKE ALL ON FUNCTION '+signature+' FROM PUBLIC;GRANT EXECUTE ON FUNCTION '+signature+' TO '+s.base.ident(s.logins(plan.run_id)['auth'])+';'
    with lease.connect('bootstrap') as observer,lease.connect('auth') as caller:
        check('hash_queue_control_starts_empty',observer.execute('SELECT count(*) FROM identity_control.lab3_hash_jobs').fetchone()[0]==0)
        observer.execute('BEGIN READ WRITE');observer.execute(ddl,prepare=False);observer.execute('COMMIT')
        def call(function,mode,jid):
            b.require(function in (OLD,'lab3_hash_admission'),'fixed_queue_control_function')
            caller.execute('BEGIN READ WRITE')
            value=caller.execute('SELECT identity_api.'+function+'(%s,%s,%s,%s,%s)',(ticket,mode,jid,wid,ct)).fetchone()[0]
            caller.execute('COMMIT');rows.append({'function':function,'mode':mode,'id':jid,'result':value});return value
        def counts():
            return observer.execute("SELECT count(*) FILTER(WHERE state='active'),count(*) FILTER(WHERE state='queued'),count(*) FILTER(WHERE state='queued' AND clock_timestamp()>=deadline) FROM identity_control.lab3_hash_jobs").fetchone()
        def expire(ids,may_be_gone=False):
            deadline=observer.execute('SELECT max(deadline) FROM identity_control.lab3_hash_jobs WHERE id=ANY(%s)',(ids,)).fetchone()[0]
            if deadline is None and may_be_gone:return
            b.require(deadline is not None,'queue_deadline_fixture_missing')
            stop=time.monotonic()+5
            while observer.execute('SELECT clock_timestamp()').fetchone()[0]<=deadline+timedelta(milliseconds=100):
                b.require(time.monotonic()<stop,'queue_expiry_control_watchdog');time.sleep(.05)
        new='lab3_hash_admission'
        try:
            for jid in (950001,950002):b.require(call(OLD,'enter',jid)['state']=='active','old_control_active_reservation')
            check('hash_expected_old_queue_control',call(OLD,'enter',950003)['state']=='queued',{'control_only':True})
            expire([950003])
            for jid in (950001,950002):call(OLD,'finish',jid)
            result=call(OLD,'enter',950004);observed=counts()
            check('hash_expected_old_orphan_stalls_recovery',result['state']=='queued' and observed[0]==0 and observed[1]==2 and observed[2]>=1,
                {'control_only':True,'active':observed[0],'queued':observed[1],'expired_queued':observed[2]})
            # No state reset or deadline extension between old and fixed paths.
            expire([950003,950004]);result=call(new,'enter',950005)
            check('hash_expired_orphan_reclaimed_before_fresh_admission',result['state']=='active' and counts()==(1,0,0))
            check('hash_old_orphan_rows_removed',observer.execute('SELECT count(*) FROM identity_control.lab3_hash_jobs WHERE id=ANY(%s)',([950003,950004],)).fetchone()[0]==0)
            call(new,'finish',950005)
            for jid in (950006,950007):b.require(call(new,'enter',jid)['state']=='active','fixed_control_active_reservation')
            expire([950006,950007]);result=call(new,'enter',950008)
            check('hash_queue_TTL_never_reaps_executing_work',result['state']=='queued' and counts()[:2]==(2,1))
            for jid in (950006,950007):call(new,'finish',jid)
            expire([950008],may_be_gone=True);check('hash_natural_expiry_restores_admission',call(new,'enter',950009)['state']=='active')
            call(new,'finish',950009);check('hash_queue_counterexample_finishes_empty',counts()==(0,0,0))
        finally:
            observer.execute('BEGIN READ WRITE');observer.execute('DROP FUNCTION '+signature);observer.execute('COMMIT')
    result={'old_control_reproduced':True,'fresh_recovery_restored':True,'executing_reservations_not_reaped':True,
        'quota_reset':False,'operation_deadline_extended':False,'old_function_removed':True,
        'old_definition_sha256':hashlib.sha256(old.encode()).hexdigest(),'rows':rows}
    b.write(plan.run_root/'HASH-QUEUE-RECOVERY.json',result)
    return {k:v for k,v in result.items() if k!='rows'}
