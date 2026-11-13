"""Fixed restricted Argon2 worker; credentials/PHC only over protected pipe."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import common as b
b.runtime_dependencies();sys.path[:0]=[str(b.BASE),str(b.ROOT/'tools/pr03b')]
import ctypes as c,json,threading,time,re,traceback
from ctypes import wintypes as w
from concurrent.futures import ThreadPoolExecutor
import windows,crypto,admission
def main():
    b.require(len(sys.argv)==3 and sys.argv[1].startswith(r'\\.\pipe\PR03b-S3-') and sys.argv[2].isdigit(),'worker_arguments')
    windows.protect_process();k,a=windows.api();b.require(k.WaitNamedPipeW(sys.argv[1],5000),'worker_pipe_wait')
    pipe=k.CreateFileW(sys.argv[1],0xc0000000,0,None,3,0,None);b.require(pipe not in (None,c.c_void_p(-1).value),'worker_pipe_open')
    lock=threading.Lock();seq=0
    def rpc(value,segments=None):
        began=time.monotonic();acquired=None
        try:
            with admission.locked(lock):
                acquired=time.monotonic();windows.send(k,pipe,value);answer=windows.receive(k,pipe)
                if segments is not None:segments['parent_pool_transaction_ms']=segments.get('parent_pool_transaction_ms',0)+answer.get('parent_pool_transaction_ms',0)
                return answer
        finally:
            if segments is not None:
                ended=time.monotonic()
                segments['IPC_lock_ms']=segments.get('IPC_lock_ms',0)+((acquired or ended)-began)*1000
                if acquired is not None:segments['IPC_exchange_ms']=segments.get('IPC_exchange_ms',0)+(ended-acquired)*1000
    try:
        pid=w.ULONG();b.require(k.GetNamedPipeServerProcessId(pipe,c.byref(pid)) and pid.value==int(sys.argv[2]),'worker_server_PID')
        config=rpc({'kind':'hello'});b.require(config['threads'] in (1,2,4,8,16) and 1<=config['seconds']<=720,'fixed_worker_budget')
        start_at=config['start_at'];b.require(time.monotonic()-10<=start_at<=time.monotonic()+1100,'fixed_worker_start_time')
        end=start_at+config['seconds'];rows=[];row_lock=threading.Lock();loops=[];gate=admission.Bounded(shared_slots=admission.SharedSlots(config['ingress_name']));barrier=threading.Barrier(config['threads'])
        def run(index):
            nonlocal seq
            barrier.wait(5)
            delay=start_at-time.monotonic()
            if delay>0:time.sleep(delay)
            loop_start=time.monotonic()
            while time.monotonic()<end:
                with row_lock:seq+=1;jid=config['worker_index']*1000000+seq
                started=time.monotonic();detail={'expensive_calls':0,'hash_ms':0,'reason':'pending','segments':{}}
                def work(jid=jid,started=started,detail=detail):
                    answer={'state':'none'}
                    def admit(mode):
                        before=time.monotonic()
                        result=rpc({'kind':'admit','mode':mode,'id':jid,'deadline':started+1},detail['segments'])
                        detail['segments']['IPC_PG_ms']=detail['segments'].get('IPC_PG_ms',0)+(time.monotonic()-before)*1000
                        return result
                    try:
                        admission.live();answer=admit('enter')
                        while answer['state']=='queued':
                            time.sleep(min(.01,admission.live()));answer=admit('poll')
                        if answer['state']!='active':detail['reason']=answer.get('reason') or 'PG_rejected';return False
                        admission.live();admission.granted();before=time.monotonic();detail['expensive_calls']=1;detail['hash_started_ms']=(before-started)*1000
                        crypto.verify(config['phc'],config['password']);detail['hash_ms']=(time.monotonic()-before)*1000
                        detail['reason']='completed';return True
                    finally:
                        # Cleanup uses a separate bounded phase; it NEVER frees
                        # an active slot before the actual Argon2 call returns.
                        if answer['state'] in ('active','queued'):
                            token=admission.CURRENT.set(time.monotonic()+5)
                            try:rpc({'kind':'admit','mode':'finish' if answer['state']=='active' else 'cancel','id':jid,'deadline':started+1})
                            finally:admission.CURRENT.reset(token)
                try:admitted=gate.call(work,deadline=started+1)
                except admission.Expired as ex:admitted=False;detail['reason']=str(ex)
                row={'started_monotonic':started,'allowed':admitted,'client_timed_out':not admitted and detail['expensive_calls']>0,'ms':round((time.monotonic()-started)*1000,3),'detail':detail}
                with row_lock:rows.append(row)
                if not admitted:time.sleep(.02)
            with row_lock:loops.append({'thread':index,'loop_start':loop_start,'loop_end':time.monotonic(),'scheduled_start':start_at,'scheduled_end':end})
        with ThreadPoolExecutor(max_workers=config['threads']) as pool:list(pool.map(run,range(config['threads'])))
        gate.close()
        for row in rows:
            row.update(expensive_calls=row['detail']['expensive_calls'],hash_ms=round(row['detail']['hash_ms'],3),queue_ms=row['detail'].get('hash_started_ms'),reason=row['detail']['reason'],segments=row['detail']['segments'])
            row['actually_admitted']=row['expensive_calls']>0
            del row['detail']
        for offset in range(0,len(rows),50):rpc({'kind':'rows','rows':rows[offset:offset+50]})
        rpc({'kind':'done','loops':loops});print('{"status":"WORKER_DONE","secrets_exported":false}');return 0
    finally:k.CloseHandle(pipe)
if __name__=='__main__':
    try:code=main()
    except BaseException as ex:
        message=str(ex)
        safe=message if isinstance(ex,b.Stop) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,180}',message) else 'sanitized'
        print(json.dumps({'status':'STOP','error_type':type(ex).__name__,'safe_error':safe,'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(ex.__traceback__)]}));code=1
    raise SystemExit(code)
