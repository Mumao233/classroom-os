"""Fixed HTTPS/WSS load client, separated from SUT CPU/RAM quotas."""
import sys,json,time,threading,http.client,ssl,ctypes as c,uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes as w
sys.path.insert(0,str(Path(__file__).resolve().parent))
import common as b
import windows
def phase_for(seconds):
    return 'normal' if seconds<1000 else 'overload' if seconds<1600 else 'transition' if seconds<1660 else 'recovery'
def main():
    b.runtime_dependencies();sys.path[:0]=[str(b.BASE),str(b.ROOT/'tools/pr03b')]
    b.require(len(sys.argv)==3 and sys.argv[1].startswith(r'\\.\pipe\PR03b-S3-') and sys.argv[2].isdigit(),'fixed_load_driver_arguments')
    windows.protect_process();k,a=windows.api();b.require(k.WaitNamedPipeW(sys.argv[1],5000),'load_pipe_wait')
    h=k.CreateFileW(sys.argv[1],0xc0000000,0,None,3,0,None);b.require(h not in (None,c.c_void_p(-1).value),'load_pipe_open')
    try:
        pid=w.ULONG();b.require(k.GetNamedPipeServerProcessId(h,c.byref(pid)) and pid.value==int(sys.argv[2]),'load_server_kernel_PID')
        windows.send(k,h,{'kind':'hello'});cfg=windows.receive(k,h)
        b.require(cfg['scenario'] in ('nat','preauth_excess','mixed','mixed_continuous') and 1024<cfg['port']<65536 and cfg['port']!=5432,'fixed_load_scenario')
        ctx=ssl.create_default_context(cadata=cfg['CA']);rows=[];lock=threading.Lock();start=time.monotonic();sender_stop=threading.Event();sender_error=[];sender=None
        def stream_rows():
            sent=0
            try:
                while True:
                    with lock:chunk=rows[sent:sent+100];total=len(rows)
                    if chunk:
                        windows.send(k,h,{'kind':'rows','rows':chunk});b.require(windows.receive(k,h)=={'received':True},'load_result_ack');sent+=len(chunk)
                    elif sender_stop.is_set() and sent==total:break
                    else:sender_stop.wait(.25)
            except BaseException as ex:sender_error.append(type(ex).__name__)
        def call_http(path,body=None,headers=None,request_ids=None):
            conn=http.client.HTTPSConnection('127.0.0.1',cfg['port'],context=ctx,timeout=5)
            request_id=uuid.uuid4().hex
            hs={'Origin':cfg['origin'],'Content-Type':'application/json','X-Lab-Request-ID':request_id};hs.update(headers or {})
            if request_ids is not None:request_ids.append(request_id)
            try:
                conn.request('GET' if body is None else 'POST',path,None if body is None else json.dumps(body).encode(),hs)
                response=conn.getresponse();value=json.loads(response.read());return response.status,value,dict(response.getheaders())
            finally:conn.close()
        def task(kind,index,scheduled):
            began=time.monotonic();phase=phase_for(scheduled)
            row={'kind':kind,'phase':phase,'index':index,'scheduled_s':scheduled,'arrived_s':began-start,'dispatch_lag_ms':max(0,(began-start-scheduled)*1000),'status':None,'timeout':False,'request_ids':[]}
            try:
                if kind in ('login','nat'):
                    status,pre,hs=call_http('/lab/preauth',request_ids=row['request_ids'])
                    if status!=200:row.update(status=status,login_stage='preauth')
                    else:
                        name=cfg.get('users',['synthetic-student'])[index%len(cfg.get('users',['synthetic-student']))]
                        status,value,_=call_http('/lab/login',{'username':name,'password':cfg['password'],'remember_me':False},{'Cookie':hs['Set-Cookie'].split(';',1)[0],'X-CSRF-Token':pre['csrf']},request_ids=row['request_ids'])
                        row.update(status=status,authenticated=value.get('state')=='authenticated',login_stage='complete_login')
                elif kind=='preauth':row['status']=call_http('/lab/preauth',request_ids=row['request_ids'])[0]
                elif kind=='business':
                    token=cfg['sessions'][index%len(cfg['sessions'])]
                    row['status']=call_http('/lab/business',headers={'Cookie':'__Host-classroom_session='+token},request_ids=row['request_ids'])[0]
            except BaseException as ex:row.update(error_type=type(ex).__name__,timeout=isinstance(ex,TimeoutError))
            row['ms']=(time.monotonic()-began)*1000
            with lock:rows.append(row)
        sockets=[]
        try:
            if cfg['scenario'] in ('mixed','mixed_continuous'):
                from websockets.sync.client import connect
                for token,csrf in zip(cfg['sessions'],cfg['csrf']):
                    status,body,_=call_http('/lab/ws-ticket',{}, {'Cookie':'__Host-classroom_session='+token,'X-CSRF-Token':csrf})
                    b.require(status==200 and 'ticket' in body,'mixed_fresh_ticket')
                    ticket=body['ticket']
                    ws=connect('wss://127.0.0.1:'+str(cfg['ws_port'])+'/lab/ws',ssl=ctx,origin=cfg['origin'],subprotocols=['classroom.v1'],additional_headers={'Cookie':'__Host-classroom_session='+token},open_timeout=5,close_timeout=2)
                    ws.send(json.dumps({'type':'authenticate','ticket':ticket}));b.require(json.loads(ws.recv(timeout=5))['type']=='authenticated','mixed_WS_control');sockets.append(ws)
                b.require(len(sockets)==30,'mixed_30_live_WS')
            start=time.monotonic()+1;futures=[]
            if cfg['scenario'] in ('mixed','mixed_continuous'):
                phase='continuous_ready' if cfg['scenario']=='mixed_continuous' else 'baseline_ready'
                windows.send(k,h,{'kind':'phase','phase':phase,'start_monotonic':start});b.require(windows.receive(k,h)=={'received':True},'fixed_phase_ack')
            sender=threading.Thread(target=stream_rows,daemon=True);sender.start()
            with ThreadPoolExecutor(max_workers=64) as pool:
                if cfg['scenario'] in ('mixed','mixed_continuous'):
                    b.require(cfg['seconds'] in (120,1000,1780) and (cfg['scenario']!='mixed_continuous' or cfg['seconds']==1780),'fixed_mixed_duration')
                    plan=[(i/10,'business',i) for i in range(cfg['seconds']*10)]+[(float(i),'login',i) for i in range(cfg['seconds'])]
                elif cfg['scenario']=='nat':plan=[(float(i),'nat',i) for i in range(60)]
                else:plan=[(i*.25,'preauth',i) for i in range(121)]
                for at,kind,index in sorted(plan):
                    wait=start+at-time.monotonic()
                    if wait>0:time.sleep(wait)
                    futures.append(pool.submit(task,kind,index,at))
                for future in futures:future.result()
        finally:
            sender_stop.set()
            if sender is not None:sender.join(10)
            for ws in sockets:ws.close()
        b.require(sender is not None and not sender.is_alive() and not sender_error,'load_raw_stream_incomplete')
        windows.send(k,h,{'kind':'done','seconds':time.monotonic()-start,'WS_opened':len(sockets)});windows.receive(k,h)
        print('{"status":"LOAD_DONE","secrets_exported":false}');return 0
    finally:k.CloseHandle(h)
if __name__=='__main__':
    try:code=main()
    except BaseException as ex:print(json.dumps({'status':'STOP','error_type':type(ex).__name__,'safe_error':str(ex) if isinstance(ex,b.Stop) else 'sanitized'}));code=1
    raise SystemExit(code)
