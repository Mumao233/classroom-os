"""R01/R03/R07 fixed synthetic schedules; never a general executor."""
from datetime import datetime,timedelta,timezone
import json,socket,threading,time
import common as b,cases,crypto,protocol,schema as s,transport

def unstarted_cleanup_lock_timeout(lease,check,plan):
    """Real old/new 55P03 barrier, never starts an expensive operation."""
    import ast,os,admission,native_process
    from service_pool import FixedPool
    old_raw=b.read(b.HOME.parent/'PR03bS3Mechanisms45'/'admission.py')
    b.require(b.sha(old_raw)=='2dc476f17013f7c6fe7bd2de6ace5393b3cb7d97e034af01941c77bdcb192da3','old45_reclaim_source_pin')
    node=next(n for n in ast.parse(old_raw).body if isinstance(n,ast.FunctionDef) and n.name=='reclaim_unstarted')
    ns={'b':b,'time':time,'CURRENT':admission.CURRENT}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'frozen45_reclaim','exec'),ns)
    wid=os.getpid();ct=str(native_process.observe_process_details(wid).identity.creation_token);rows=[]
    with FixedPool(lease,('auth',)) as pool,lease.connect('bootstrap') as holder,lease.connect('bootstrap') as observer:
        for _ in range(2):pool.database('auth','SELECT 1',read_only=True)
        for repeat in range(20):
            for variant in ('old','fixed'):
                jid=900000000+repeat*2+(variant=='fixed');first_timeout=threading.Event();done=threading.Event();box={};calls=[]
                def control(mode):
                    calls.append({'mode':mode,'cleanup_deadline':admission.CURRENT.get(),'monotonic':time.monotonic()})
                    try:return pool.database('auth','SELECT identity_api.lab3_hash_admission(%s,%s,%s,%s,%s)',(s.ticket('auth',2),mode,jid,wid,ct))
                    except BaseException as ex:
                        if getattr(ex,'sqlstate',None)=='55P03':first_timeout.set()
                        raise
                b.require(control('enter')['state']=='active','cleanup_control_actual_active_reservation')
                calls.clear();holder.execute('BEGIN READ WRITE');holder.execute('SELECT pg_advisory_xact_lock(938448889)')
                def work():
                    expired=time.monotonic()-1;token=admission.CURRENT.set(expired);began=time.monotonic()
                    try:
                        (ns['reclaim_unstarted'] if variant=='old' else admission.reclaim_unstarted)(control,expensive_calls=0)
                        box['completed']=True
                    except BaseException as ex:box.update(error_type=type(ex).__name__,sqlstate=getattr(ex,'sqlstate',None))
                    finally:
                        box.update(seconds=time.monotonic()-began,original_expired_deadline_restored=admission.CURRENT.get()==expired)
                        admission.CURRENT.reset(token);done.set()
                thread=threading.Thread(target=work);thread.start()
                try:
                    b.require(first_timeout.wait(15),'cleanup_real_55P03_not_observed')
                    if variant=='old':b.require(done.wait(5),'old_cleanup_must_exit_after_55P03')
                finally:holder.execute('COMMIT')
                b.require(done.wait(10),'cleanup_fixed_barrier_watchdog');thread.join(1)
                count=observer.execute('SELECT count(*) FROM identity_control.lab3_hash_jobs WHERE id=%s',(jid,)).fetchone()[0]
                if variant=='old':
                    check('R02_old_cleanup_55P03_aborts_leaving_owned_unstarted:'+str(repeat),box.get('sqlstate')=='55P03' and count==1 and not box.get('completed'))
                    admission.reclaim_unstarted(control,expensive_calls=0)
                else:
                    check('R02_fixed_cleanup_55P03_retries_same_owned_only:'+str(repeat),box.get('completed') is True and count==0 and box['seconds']<=5 and len({v['cleanup_deadline'] for v in calls})==1 and box['original_expired_deadline_restored'])
                rows.append({'repeat':repeat,'variant':variant,'result':box,'calls':calls,'expensive_calls':0,'owned_reservation_after':count,'actual_55P03':True})
        check('R02_cleanup_lock_control_finishes_empty',observer.execute('SELECT count(*) FROM identity_control.lab3_hash_jobs').fetchone()[0]==0)
    result={'rows':rows,'old_controls':20,'fixed_controls':20,'cleanup_budget_seconds':5,'operation_authorization_renewed':False,'hashes_started':0,'capacity_or_full_worker_lifecycle_PASS':False}
    b.write(plan.run_root/'R02-UNSTARTED-CLEANUP-LOCK.json',result)
    return {k:v for k,v in result.items() if k!='rows'}

def source_reader_concurrency(lease,check,plan):
    from concurrent.futures import ThreadPoolExecutor
    import admission,source_pin
    pin=b._source_pin;b.require(pin is not None,'source_pin_required');rows=[]
    before=pin.statistics()
    for repeat in range(20):
        barrier=threading.Barrier(16)
        def verify(_):
            barrier.wait(5);began=time.monotonic();token=admission.CURRENT.set(began+1)
            try:return {'digest':b.bundle(),'seconds':time.monotonic()-began}
            finally:admission.CURRENT.reset(token)
        with ThreadPoolExecutor(max_workers=16) as pool:values=list(pool.map(verify,range(16)))
        check('R02_native_parallel_source_complete_hashes:'+str(repeat),all(v['digest']==pin.digest and v['seconds']<=1 for v in values))
        rows.append({'repeat':repeat,'readers':16,'seconds':[v['seconds'] for v in values]})
    after=pin.statistics()
    check('R02_native_source_readers_not_serialized',2<=after['peak_borrowed']<=16 and after['borrowed']==0 and after['active']==0)
    check('R02_native_source_all_calls_reread',after['completed']-before['completed']==320)
    denials=pin.deny_probe();check('R02_source_write_delete_pins_retained',len(denials)==2*len(pin.files)+1 and all(v['winerror']==32 for v in denials))
    clone=source_pin.Bundle(b.HOME,pin.digest)
    try:
        handle,identity,_=clone.files['selftest.py'];clone.files['selftest.py']=(handle,identity,'0'*64)
        try:clone.verify()
        except b.Stop as ex:check('R02_native_wrong_expected_source_hash_denied',str(ex)=='pinned_source_content_changed')
        else:check('R02_native_wrong_expected_source_hash_denied',False)
    finally:clone.close()
    check('R02_native_independent_source_handles_closed',clone.closed and clone.active==0)
    b.write(plan.run_root/'R02-SOURCE-PIN-CONCURRENCY.json',{'rows':rows,'before':before,'after':after,'write_delete_denials':denials,'wrong_digest_denied':True,'source_bytes_modified':False,'cached_source_bytes':False})
    return {'repeats':20,'readers_each':16,'complete_verifications':320,'native_peak_parallel_readers':after['peak_borrowed'],'source_write_delete_denied':True,'source_byte_cache':False}

def login_smoke(lease,check,plan):
    root=b.HOME/'R02-LOGIN-TLS-private';root.mkdir();lab=transport.Lab(lease,root);rows=[]
    try:
        for i in range(3):
            started=time.monotonic();code,pre,headers=transport.request(lab,'/lab/preauth')
            check('R02_complete_chain_preauth:'+str(i),code==200)
            code,value,_=transport.request(lab,'/lab/login',{'username':'synthetic-student','password':lab.password,'remember_me':False},csrf=pre['csrf'],headers={'Cookie':headers['Set-Cookie'].split(';',1)[0]})
            rows.append({'kind':'correct','ms':(time.monotonic()-started)*1000,'status':code})
            check('R02_complete_chain_valid_control:'+str(i),code==200 and value.get('state')=='authenticated')
        code,pre,headers=transport.request(lab,'/lab/preauth');check('R02_failure_context',code==200)
        for name in ('synthetic-student','absent@example.invalid'):
            for i in range(4):
                started=time.monotonic();code,value,_=transport.request(lab,'/lab/login',{'username':name,'password':'synthetic-wrong','remember_me':False},csrf=pre['csrf'],headers={'Cookie':headers['Set-Cookie'].split(';',1)[0]})
                rows.append({'kind':'wrong-known' if name=='synthetic-student' else 'unknown','ms':(time.monotonic()-started)*1000,'status':code})
                check('R02_actual_failure_budget:'+str(len(rows)),code==(401 if i<3 else 429))
        return {'rows':rows,'capacity_or_enumeration_PASS':False}
    finally:
        b.write(plan.run_root/'R02-LOGIN-SMOKE.json',{'rows':rows,'evidence_level':'functional diagnostic; not tail acceptance'})
        lab.close()

def revoked_children(lease,check,plan,env):
    import windows
    d=protocol.CURRENT_FIXTURE
    for repeat in range(20):
        for enabled in (False,True):
            new=dict(d.manifest,version=d.highwater['version']+1,enabled=enabled)
            body=d.change_body(new);sig=d.change.sign(crypto.signed('classroom.identity.change.v1',body))
            with lease.connect('bootstrap') as c:old=c.execute('SELECT version,manifest_hash FROM identity_control.lab_guard WHERE id=1').fetchone()
            with d.gate.hold(exclusive=True):
                d.reserve(body,sig,new)
                with lease.connect('maintenance') as c:
                    c.execute('BEGIN READ WRITE');c.execute('SELECT identity_api.lab3_registration(%s,%s,%s,%s,%s,%s,%s)',(s.ticket('maintenance',5),*old,new['version'],d.digest(new),body['approval_nonce'],enabled));c.execute('COMMIT')
                d.publish(new)
            if not enabled:
                windows.ipc_experiment(plan,env,check,label='D0-revoked-'+str(repeat).zfill(2),d0=d,pg=(new['version'],d.digest(new)),expect_revoked=True)
    return {'real_reduced_children':20,'revoked_signed_manifest_checked':True,'signatures_emitted':0,'private_keys_delivered':0,'real_D0_enrollment':False}

def ledger_restart(lease,check,plan,env,*,fresh_children=False):
    import windows
    b.require(type(fresh_children) is bool,'fixed_persistent_process_schedule')
    d=protocol.CURRENT_FIXTURE
    for repeat in range(20):
        new=dict(d.manifest,version=d.highwater['version']+1)
        body=d.change_body(new);sig=d.change.sign(crypto.signed('classroom.identity.change.v1',body))
        with d.gate.hold(exclusive=True):
            d.reserve(body,sig,new)
            restored=protocol.D0.reopen(d)
            with lease.connect('bootstrap') as c:old=c.execute('SELECT version,manifest_hash FROM identity_control.lab_guard WHERE id=1').fetchone()
            if fresh_children:windows.ipc_experiment(plan,env,check,label='D0-reserved-'+str(repeat).zfill(2),d0=restored,pg=old,expect_reserved=True)
            try:restored.ready(old)
            except b.Stop:check('R07_restart_reserved_phase_closed:'+str(repeat),True)
            else:check('R07_restart_reserved_phase_closed:'+str(repeat),False)
            check('R07_restart_nonce_persisted:'+str(repeat),body['approval_nonce'] in restored.highwater['nonces'])
            with lease.connect('maintenance') as c:
                c.execute('BEGIN READ WRITE');c.execute('SELECT identity_api.lab3_registration(%s,%s,%s,%s,%s,%s,%s)',(s.base.ticket('maintenance',5),*old,new['version'],d.digest(new),body['approval_nonce'],True));c.execute('COMMIT')
            restored.publish(new);again=protocol.D0.reopen(restored)
            with lease.connect('bootstrap') as c:pg=c.execute('SELECT version,manifest_hash FROM identity_control.lab_guard WHERE id=1').fetchone()
            check('R07_restart_forward_same_approval_ready:'+str(repeat),again.ready(pg))
            try:again.reserve(body,sig,new)
            except b.Stop:check('R07_restart_approval_replay_denied:'+str(repeat),True)
            else:check('R07_restart_approval_replay_denied:'+str(repeat),False)
        d=again
        if fresh_children:windows.ipc_experiment(plan,env,check,label='D0-restart-ready-'+str(repeat).zfill(2),d0=d,pg=pg)
    protocol.CURRENT_FIXTURE=d
    windows.ipc_experiment(plan,env,check,label='D0-verifier',d0=d,pg=pg)
    return {'persistent_reload_repeats':20,'fresh_reduced_child_reads_disk':True,'fresh_processes_reserved':20 if fresh_children else 0,'fresh_processes_ready':20 if fresh_children else 1,'external_installer_pins_preserved':True,'coordinator_object_reload_not_VM_restart':True,'real_D0':False}

def revocation(lease,check):
    d=protocol.CURRENT_FIXTURE
    def pg():
        with lease.connect('bootstrap') as c:return c.execute('SELECT version,manifest_hash FROM identity_control.lab_guard WHERE id=1').fetchone()
    def transition(enabled):
        new=dict(d.manifest,version=d.highwater['version']+1,enabled=enabled)
        body=d.change_body(new);sig=d.change.sign(crypto.signed('classroom.identity.change.v1',body));old=pg()
        with d.gate.hold(exclusive=True):
            d.reserve(body,sig,new)
            with lease.connect('maintenance') as c:
                c.execute('BEGIN READ WRITE');c.execute('SELECT identity_api.lab3_registration(%s,%s,%s,%s,%s,%s,%s)',(s.base.ticket('maintenance',5),*old,new['version'],d.digest(new),body['approval_nonce'],enabled));c.execute('COMMIT')
            d.publish(new)
        return new
    for repeat in range(20):
        # Fresh issue and complete projections exist BEFORE revoke. Even
        # projections made afterwards by the trusted fixture cannot authorize.
        ids=[500000+repeat*10+i for i in range(3)]
        for cid,ready in zip(ids,(False,True,True)):
            cases.fixture(lease.connect,cid,executor=5,target=3,ready=ready)
            cases.proof(lease.connect,cid,cid,executor=5,action='complete' if ready else 'issue')
        transition(False)
        try:d.ready(pg())
        except b.Stop as ex:check('R01_revoked_new_proof_denied:'+str(repeat),str(ex)=='D0_revoked')
        else:check('R01_revoked_new_proof_denied:'+str(repeat),False)
        with lease.connect('bootstrap') as c:before=cases.state(c)
        for cid,service in ((ids[0],'recovery'),(ids[1],'recovery'),(ids[2],'status')):
            with lease.connect(service) as c:
                c.execute('BEGIN READ WRITE')
                if service=='recovery':cases.denied(c,'SELECT identity_api.lab3_write(%s,%s,%s,%s)',(s.base.ticket('recovery',5),cid,'plan-digest','cap-digest'),check,'R01_revoke_first_write:'+str(cid))
                else:cases.denied(c,'SELECT identity_api.lab3_status(%s,%s,%s,%s)',(s.base.ticket('status',5),cid,cid,datetime.now(timezone.utc)+timedelta(seconds=120)),check,'R01_revoke_first_status:'+str(cid))
                c.execute('ROLLBACK')
        with lease.connect('bootstrap') as c:check('R01_reject_no_business_change:'+str(repeat),cases.state(c)==before)
        transition(True)
        # Recovery owns the shared D0 gate and all required PG locks first.
        # An exclusive maintainer must wait; no external publish under PG locks.
        cid=500500+repeat;cases.fixture(lease.connect,cid,executor=5,target=3,ready=True);cases.proof(lease.connect,cid,cid,executor=5)
        with d.gate.hold():
            task=cases.worker('D0-maintainer',lambda:transition(False))
            until=time.monotonic()+5
            while not d.gate.waiters and time.monotonic()<until:time.sleep(.005)
            check('R01_recovery_gate_first_maintenance_waits:'+str(repeat),d.gate.waiters==1 and not task[1].is_set())
            with lease.connect('recovery') as c:
                c.execute('BEGIN READ WRITE');check('R01_recovery_first_commits_once:'+str(repeat),cases.write(c,cid,executor=5)['committed']);c.execute('COMMIT')
        b.require(task[1].wait(15),'R01_maintenance_watchdog')
        check('R01_maintenance_after_recovery_commits:'+str(repeat),'value' in task[2])
        transition(True)
    # The twenty administrator recoveries intentionally advanced target 3's
    # epoch. Subsequent independent tests need a NEW synthetic verifier
    # projection, rather than silently treating that administrator's old MFA
    # qualification as current. This is trusted fixture setup, not reauth API.
    with lease.connect('bootstrap') as c:
        c.execute('BEGIN READ WRITE');c.execute('UPDATE identity_control.lab3_qualifications q SET epoch=u.epoch FROM identity_control.lab_principals u WHERE q.actor=3 AND u.id=3');c.execute('COMMIT')
    return {'orders':2,'repeats_each':20,'revocation_is_current_authorization':False,'synthetic_only':True,'next_scenario_new_synthetic_qualification':True}

def ws_failures(lease,check,plan,*,post_upgrade_only=False):
    from websockets.sync.client import connect
    from websockets.exceptions import InvalidStatus,ConnectionClosed
    b.require(type(post_upgrade_only) is bool,'fixed_WS_abort_schedule')
    root=b.HOME/('R03-POST-UPGRADE-TLS-private' if post_upgrade_only else 'R03-TLS-private');root.mkdir();lab=transport.Lab(lease,root);lab.ws_start()
    token,record=lab.login(2);uri='wss://127.0.0.1:'+str(lab.ws_port)+'/lab/ws'
    kwargs={'ssl':lab.client_context,'origin':lab.origin,'additional_headers':{'Cookie':'__Host-classroom_session='+token},'open_timeout':5,'close_timeout':2}
    trials=[]
    def drained(label):
        end=time.monotonic()+5
        while time.monotonic()<end:
            with lab.lock:counts=list(lab.ws_counts)
            if counts==[0,0]:break
            time.sleep(.01)
        check(label,counts==[0,0],{'counts':counts})
    try:
        for mode in (('post_upgrade_disconnect',) if post_upgrade_only else ('missing_subprotocol','wrong_subprotocol','malformed_upgrade','disconnect')):
            for repeat in range(20):
                for i in range(8):
                    if mode in ('missing_subprotocol','wrong_subprotocol'):
                        try:
                            with transport.diagnostic_connect(lab,uri,context_label=mode+'-'+str(repeat)+'-'+str(i),subprotocols=None if mode=='missing_subprotocol' else ['incorrect.v1'],**kwargs):check('R03_invalid_handshake_accepted',False)
                        except InvalidStatus as ex:check('R03_handshake_terminal:'+mode+':'+str(repeat)+':'+str(i),ex.response.status_code==400)
                    else:
                        raw=socket.create_connection(('127.0.0.1',lab.ws_port),timeout=5)
                        with lab.client_context.wrap_socket(raw,server_hostname='127.0.0.1') as sock:
                            if mode=='malformed_upgrade':
                                payload=('GET /lab/ws HTTP/1.1\r\nHost: app.test\r\nOrigin: '+lab.origin+'\r\nCookie: __Host-classroom_session='+token+'\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: invalid\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: classroom.v1\r\n\r\n').encode()
                                sock.sendall(payload);response=sock.recv(4096)
                                check('R03_malformed_terminal:'+str(repeat)+':'+str(i),response.startswith(b'HTTP/1.1 400'))
                            elif mode=='post_upgrade_disconnect':
                                payload=('GET /lab/ws HTTP/1.1\r\nHost: app.test\r\nOrigin: '+lab.origin+'\r\nCookie: __Host-classroom_session='+token+'\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: classroom.v1\r\n\r\n').encode()
                                sock.sendall(payload);response=b''
                                while b'\r\n\r\n' not in response and len(response)<4096:
                                    chunk=sock.recv(4096-len(response))
                                    if not chunk:break
                                    response+=chunk
                                check('R03_actual_upgrade_before_abrupt_disconnect:'+str(repeat)+':'+str(i),response.startswith(b'HTTP/1.1 101 ') and b'\r\n\r\n' in response)
                                sock.shutdown(socket.SHUT_RDWR)
                            else:sock.sendall(b'GET /lab/ws HTTP/1.1\r\n')
                drained('R03_all_failures_released:'+mode+':'+str(repeat))
                ticket=lab.ticket(token)
                with transport.diagnostic_connect(lab,uri,context_label='legitimate-'+mode+'-'+str(repeat),subprotocols=['classroom.v1'],**kwargs) as ws:
                    check('R06_actual_TLS12_profile:'+mode+':'+str(repeat),ws.socket.version()=='TLSv1.2')
                    ws.send(json.dumps({'type':'authenticate','ticket':ticket}));check('R03_legitimate_after_failure:'+mode+':'+str(repeat),b.strict(ws.recv(timeout=5).encode())['type']=='authenticated')
                drained('R03_handler_exit_released:'+mode+':'+str(repeat))
                trials.append({'mode':mode,'repeat':repeat,'failures':8,'final_counts':list(lab.ws_counts)})
        return {'trials':len(trials),'negative_handshakes':len(trials)*8,'all_reservations_released':True}
    finally:
        b.write(plan.run_root/('R03-POST-UPGRADE-ABORTS.json' if post_upgrade_only else 'R03-WS-FAILURES.json'),{'trials':trials,'trace':lab.ws_trace,'historical_timeout_cause':'UNDETERMINED','captured_before_lab_cleanup':True})
        lab.close()

def failures(lease,check,plan):
    # issue expiry and audit failure are separate from complete/status waits.
    for repeat in range(20):
        cid=510000+repeat;cases.fixture(lease.connect,cid);cases.proof(lease.connect,cid,cid,action='issue',age=121)
        with lease.connect('recovery') as c:
            c.execute('BEGIN READ WRITE');cases.denied(c,'SELECT identity_api.lab3_write(%s,%s,%s,%s)',(s.base.ticket('recovery',3),cid,'plan-digest','cap-digest'),check,'R07_issue_expired:'+str(repeat));c.execute('ROLLBACK')
        # A fixed audit collision forces late failure and complete rollback.
        cid=511000+repeat;cases.fixture(lease.connect,cid);cases.proof(lease.connect,cid,cid,action='issue')
        with lease.connect('bootstrap') as c:
            c.execute('BEGIN READ WRITE');c.execute("INSERT INTO identity_control.lab3_audit VALUES(%s,%s,'injected-prior-audit')",(cid,cid));c.execute('COMMIT');before=cases.state(c)
        with lease.connect('recovery') as c:
            c.execute('BEGIN READ WRITE');cases.denied(c,'SELECT identity_api.lab3_write(%s,%s,%s,%s)',(s.base.ticket('recovery',3),cid,'plan-digest','cap-digest'),check,'R07_issue_audit_atomic_rollback:'+str(repeat),wanted='23505');c.execute('ROLLBACK')
        with lease.connect('bootstrap') as c:check('R07_issue_audit_zero_change:'+str(repeat),cases.state(c)==before)
    # Actual DB-time expiry in the same function that consumes the MFA step.
    for repeat in range(20):
        for relation in ('before','equal','after'):
            with lease.connect('session') as c:
                c.execute('BEGIN READ WRITE')
                if relation=='before':
                    until=c.execute("SELECT clock_timestamp()+interval '5 seconds'").fetchone()[0]
                    value=c.execute('SELECT identity_api.lab3_mfa_step(%s,%s,%s)',(s.base.ticket('session',2),900000+repeat,until)).fetchone()[0]
                    check('R07_MFA_before:'+str(repeat),value<until);c.execute('ROLLBACK')
                else:
                    # equality uses a single server expression with no fake
                    # machine clock; actual verification can only be >= it.
                    cases.denied(c,"SELECT identity_api.lab3_mfa_step(%s,%s,clock_timestamp()-(%s*interval '1 second'))",(s.base.ticket('session',2),900000+repeat,0 if relation=='equal' else 1),check,'R07_MFA_'+relation+':'+str(repeat));c.execute('ROLLBACK')
        with lease.connect('bootstrap') as blocker,lease.connect('bootstrap') as observer,lease.connect('session') as c:
            blocker.execute('BEGIN READ WRITE');blocker.execute('SELECT * FROM identity_control.lab3_steps WHERE owner=2 FOR UPDATE')
            c.execute('BEGIN READ WRITE');until=c.execute("SELECT clock_timestamp()+interval '0.4 seconds'").fetchone()[0]
            task=cases.worker('MFA-expiry-cross',lambda:c.execute('SELECT identity_api.lab3_mfa_step(%s,%s,%s)',(s.base.ticket('session',2),900000+repeat,until)).fetchone()[0])
            check('R07_MFA_actually_blocked:'+str(repeat),cases.wait_blocked(observer,c.c.info.backend_pid))
            while observer.execute('SELECT clock_timestamp()').fetchone()[0]<=until:time.sleep(.005)
            blocker.execute('ROLLBACK');b.require(task[1].wait(5),'MFA_expiry_watchdog');check('R07_MFA_short_wait_cross_expiry:'+str(repeat),task[2].get('sqlstate')=='42501');c.execute('ROLLBACK')
    return {'issue_expiry':20,'issue_audit_failures':20,'MFA_boundaries_and_actual_short_wait':20,'formal_authentication_implemented':False}

def secret_response_loss(lease,check,plan):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.asymmetric import ec
    import secrets,hashlib
    rows=[]
    for repeat in range(20):
        cid=512000+repeat;cases.fixture(lease.connect,cid);cases.proof(lease.connect,cid,cid,action='issue')
        cap=secrets.token_urlsafe(32);cap_hash=hashlib.sha256(cap.encode()).hexdigest()
        with lease.connect('recovery') as c:
            c.execute('BEGIN READ WRITE');cases.write(c,cid,cap=cap_hash);c.execute('COMMIT')
        # Fixed response-drop seam AFTER commit, BEFORE browser delivery. No
        # raw cap/seed is stored in a generic receipt or exported to evidence.
        receiver=ec.generate_private_key(ec.SECP256R1());seed=secrets.token_bytes(20)
        message=crypto.signed('classroom.identity.receiver.v1',{'action':'enroll','case_id':str(cid),'flow_generation':1,'server_nonce':crypto.b64(secrets.token_bytes(32)),'request_hash':cap_hash})
        crypto.receiver_verify(crypto.jwk(receiver.public_key()),message,crypto.receiver_sign(receiver,message))
        key=AESGCM.generate_key(bit_length=256);iv=secrets.token_bytes(12);cipher=iv+AESGCM(key).encrypt(iv,seed,str(cid).encode())
        with lease.connect('bootstrap') as c:
            c.execute('BEGIN READ WRITE');c.execute("INSERT INTO identity_control.lab3_factors VALUES(1,%s,'pending',%s)",(cid,cipher));c.execute('COMMIT');before=cases.state(c)
        for query in range(3):
            with lease.connect('status') as c:
                c.execute('BEGIN READ WRITE');fact=cases.status(c,cid,cid);c.execute('ROLLBACK')
            encoded=json.dumps(fact).encode()
            check('R07_lost_cap_seed_status_only_facts:'+str(repeat)+':'+str(query),fact['result']=='committed' and cap.encode() not in encoded and seed not in encoded and set(fact)=={'result','stage','audit_id'})
        with lease.connect('recovery') as c:
            c.execute('BEGIN READ WRITE');cases.denied(c,'SELECT identity_api.lab3_write(%s,%s,%s,%s)',(s.base.ticket('recovery',3),cid,'plan-digest',cap_hash),check,'R07_lost_response_old_issue_cannot_resign:'+str(repeat));c.execute('ROLLBACK')
        with lease.connect('bootstrap') as c:
            check('R07_secret_loss_requeries_zero_mutation:'+str(repeat),cases.state(c)==before)
            check('R07_seed_material_created_once:'+str(repeat),c.execute('SELECT count(*) FROM identity_control.lab3_factors WHERE owner=1 AND generation=%s',(cid,)).fetchone()[0]==1)
        rows.append({'case':cid,'cap_response_delivered':False,'seed_response_delivered':False,'secret_replay_count':0,'fixed_receiver_PoP':True,'status_reads':3})
    b.write(plan.run_root/'R07-SECRET-RESPONSE-LOSS.json',{'rows':rows,'fault_location':'fixed_receiver_delivery_seam_after_commit','actual_HTTP_seed_endpoint':False,'secrets_exported':False})
    return {'repeats':20,'secret_replays':0,'wire_scope':'fixed receiver seam; no product seed endpoint claim'}

def recovery_code_locks(lease,check,plan):
    import hashlib,secrets
    rows=[]
    for order in ('code_first','final_first'):
        for repeat in range(20):
            cid=520000+(0 if order=='code_first' else 100)+repeat
            code_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest()
            with lease.connect('bootstrap') as c:
                c.execute('BEGIN READ WRITE');c.execute('UPDATE identity_control.lab3_groups SET revoked=true WHERE owner=1');c.execute('INSERT INTO identity_control.lab3_groups VALUES(1,%s,false,true,%s,%s)',(cid,'R07-code-'+str(cid),[code_hash]));c.execute('COMMIT')
            cases.fixture(lease.connect,cid,target=1,ready=True);cases.proof(lease.connect,cid,cid)
            with lease.connect('auth') as a,lease.connect('recovery') as r,lease.connect('bootstrap') as observer:
                a.execute('BEGIN READ WRITE');r.execute('BEGIN READ WRITE')
                code=lambda:a.execute('SELECT identity_api.lab3_code(%s,%s)',(s.ticket('auth',1),code_hash)).fetchone()[0]
                if order=='code_first':
                    check('R07_code_current_control:'+str(repeat),code())
                    task=cases.worker('final-after-code',lambda:cases.write(r,cid))
                    check('R07_final_waits_on_code_user_lock:'+str(repeat),cases.wait_blocked(observer,r.c.info.backend_pid))
                    a.execute('COMMIT');b.require(task[1].wait(5),'R07_code_first_watchdog')
                    check('R07_code_first_then_one_final:'+str(repeat),task[2].get('value',{}).get('committed') is True);r.execute('COMMIT')
                else:
                    check('R07_final_current_control:'+str(repeat),cases.write(r,cid)['committed'])
                    task=cases.worker('code-after-final',code)
                    check('R07_code_waits_on_final_user_lock:'+str(repeat),cases.wait_blocked(observer,a.c.info.backend_pid))
                    r.execute('COMMIT');b.require(task[1].wait(5),'R07_final_first_watchdog')
                    check('R07_final_first_old_code_refused:'+str(repeat),task[2].get('value') is False);a.execute('COMMIT')
                check('R07_old_group_revoked_both_orders:'+order+':'+str(repeat),observer.execute('SELECT revoked FROM identity_control.lab3_groups WHERE owner=1 AND generation=%s',(cid,)).fetchone()[0] is True)
                rows.append({'order':order,'repeat':repeat,'actual_pg_blocker_observed':True,'deadlocks':0})
    source=__import__('identity_lab').ddl(plan.run_id)
    code=source[source.index('CREATE FUNCTION identity_api.lab3_code('):source.index('ALTER FUNCTION identity_api.lab3_code(')]
    check('R07_code_lock_order_user_before_group_invariant',code.index('lab_principals')<code.index('UPDATE identity_control.lab3_groups') and 'FOR SHARE' in code)
    b.write(plan.run_root/'R07-RECOVERY-CODE-LOCKS.json',{'rows':rows,'invariant':'user SHARE before sole active group UPDATE; final user UPDATE before factor/group; changing code_hashes cannot change owner/FK/unique active-key columns','user_lock_strength_lowered':False})
    return {'orders':2,'repeats_each':20,'deadlocks':0,'implicit_FK_reverse_edge':False}

def ws_fault_matrix(lease,check,plan):
    from websockets.exceptions import InvalidStatus,ConnectionClosed
    root=b.HOME/'R06-R07-TLS-private';root.mkdir();lab=transport.Lab(lease,root);lab.ws_start();rows=[]
    token,record=lab.login(2);uri='wss://127.0.0.1:'+str(lab.ws_port)+'/lab/ws'
    options={'ssl':lab.client_context,'origin':lab.origin,'additional_headers':{'Cookie':'__Host-classroom_session='+token},'subprotocols':['classroom.v1'],'open_timeout':5,'close_timeout':2}
    def open_ws(label,**overrides):
        return transport.diagnostic_connect(lab,uri,context_label=label,**dict(options,**overrides))
    def terminal(ws,label):
        try:ws.recv(timeout=5)
        except ConnectionClosed as ex:check(label,ex.rcvd is not None and ex.rcvd.code==1008)
        else:check(label,False)
    def authenticated(label):
        ticket=lab.ticket(token);ws=open_ws(label);ws.send(json.dumps({'type':'authenticate','ticket':ticket}));check(label+':authenticated',b.strict(ws.recv(timeout=5).encode())['type']=='authenticated');return ws
    def drained(label):
        until=time.monotonic()+5
        while time.monotonic()<until:
            with lab.lock:counts=list(lab.ws_counts)
            if counts==[0,0]:break
            time.sleep(.01)
        check(label,counts==[0,0],{'counts':counts})
    try:
        for field in ('owner','session_id','epoch','classroom','fence'):
            alternate,other=lab.login(2)
            for repeat in range(20):
                ticket=lab.ticket(token)
                value={'owner':1,'session_id':other['id'],'epoch':record['epoch']+1,'classroom':11,'fence':8}[field]
                with lease.connect('bootstrap') as c:
                    c.execute('BEGIN READ WRITE');c.execute('UPDATE identity_control.lab3_tickets SET '+field+'=%s WHERE token_hash=%s',(value,transport.digest(ticket)));c.execute('COMMIT')
                with open_ws('binding-'+field+'-'+str(repeat)) as ws:
                    ws.send(json.dumps({'type':'authenticate','ticket':ticket}));terminal(ws,'R07_WS_wrong_binding:'+field+':'+str(repeat))
                rows.append({'kind':'wrong_binding','field':field,'repeat':repeat,'result':'rejected'})
        # Eight is the source cap. With the sole approved loopback source this
        # also proves the global unauthenticated count cannot exceed sixteen.
        for repeat in range(20):
            pending=[]
            try:
                for i in range(8):pending.append(open_ws('source-cap-'+str(repeat)+'-'+str(i)))
                try:open_ws('source-cap-reject-'+str(repeat))
                except InvalidStatus as ex:check('R07_WS_source_capacity_8:'+str(repeat),ex.response.status_code==403)
                else:check('R07_WS_source_capacity_8:'+str(repeat),False)
            finally:
                for ws in pending:ws.close()
            drained('R07_WS_source_capacity_cleanup:'+str(repeat))
            rows.append({'kind':'source_capacity','repeat':repeat,'capacity':8,'global_unauthenticated_upper_bound_single_source':8})
        for repeat in range(20):
            active=[]
            try:
                for i in range(64):active.append(authenticated('global-cap-'+str(repeat)+'-'+str(i)))
                try:open_ws('global-cap-reject-'+str(repeat))
                except InvalidStatus as ex:check('R07_WS_global_capacity_64:'+str(repeat),ex.response.status_code==403)
                else:check('R07_WS_global_capacity_64:'+str(repeat),False)
            finally:
                for ws in active:ws.close()
            drained('R07_WS_global_capacity_cleanup:'+str(repeat))
            rows.append({'kind':'global_capacity','repeat':repeat,'capacity':64})
        for repeat in range(20):
            with authenticated('PG-fault-live-'+str(repeat)) as ws:
                lab.fault_sql_timeout=True;began=time.monotonic()
                try:ws.send('{"type":"sensitive_probe"}');terminal(ws,'R07_WS_real_PG_timeout_fail_closed:'+str(repeat))
                finally:lab.fault_sql_timeout=False
                check('R06_actual_statement_timeout_SQLSTATE:'+str(repeat),any(v.get('sqlstate')=='57014' for v in lab.ws_trace[-6:]))
                check('R06_PG_timeout_ordinary_budget:'+str(repeat),time.monotonic()-began<=5)
            # Cookie remains current; loss of PG availability never grants a
            # ticket or an authenticated frame. Real statement failure below.
            lab.fault_sql_timeout=True
            try:
                try:open_ws('PG-fault-upgrade-'+str(repeat))
                except InvalidStatus as ex:check('R07_WS_PG_timeout_new_upgrade_denied:'+str(repeat),ex.response.status_code==403)
                else:check('R07_WS_PG_timeout_new_upgrade_denied:'+str(repeat),False)
            finally:lab.fault_sql_timeout=False
            rows.append({'kind':'actual_PG_statement_timeout','repeat':repeat,'sqlstate':'57014','ordinary_budget_seconds':5})
        # A revoked session cannot reconnect with a fresh ticket-shaped value;
        # knowing identifiers/cursor never upgrades current qualification.
        for repeat in range(20):
            current,row=lab.login(2);fresh=lab.ticket(current);lab.session(current,mode='revoke')
            try:open_ws('reconnect-revoked-'+str(repeat),additional_headers={'Cookie':'__Host-classroom_session='+current})
            except InvalidStatus as ex:check('R07_WS_revoked_reconnect_denied:'+str(repeat),ex.response.status_code==403)
            else:check('R07_WS_revoked_reconnect_denied:'+str(repeat),False)
            rows.append({'kind':'revoked_reconnect','repeat':repeat,'ticket_issued_before_revocation':True})
        return {'trials':len(rows),'all_terminal_rejections':True,'historical_HOME24_28_30':'UNDETERMINED'}
    finally:
        b.write(plan.run_root/'R06-R07-WS-MATRIX.json',{'rows':rows,'trace':lab.ws_trace,'historical_causes':'UNDETERMINED','same_connection_diagnostics':True,'captured_before_lab_cleanup':True})
        lab.close()
