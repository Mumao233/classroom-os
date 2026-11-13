"""Independent business event loop in the same bounded client cgroup."""
import asyncio,json,pathlib,ssl,sys,time,uuid
from urllib.parse import urlsplit
import httpx
cfg=json.loads(sys.stdin.readline());O=pathlib.Path(cfg['output']);raw=(O/'BUSINESS-REQUESTS.jsonl').open('w',buffering=1)
ctx=ssl.create_default_context(cafile=str(O/'root-ca.pem'))
async def run():
    clients=[]
    for item in cfg['contexts']:
        u=urlsplit(item['origin']);assert u.scheme=='https' and u.hostname=='127.0.0.1' and not u.username and not u.query and not u.fragment
        c=httpx.AsyncClient(base_url=item['origin'],verify=ctx,trust_env=False,timeout=6,limits=httpx.Limits(max_connections=32,max_keepalive_connections=8))
        # Same session as the original synthetic user, only in private memory.
        c.headers['Cookie']='__Host-classroom_session='+item['session']
        clients.append((c,item['course_id']))
    print(json.dumps(dict(ready=True)),flush=True)
    try:
        while True:
            command=json.loads(sys.stdin.readline())
            if command.get('stop'):break
            phase,start,duration=command['phase'],command['start'],command['duration'];pending=set();limit=asyncio.Semaphore(256)
            async def sample(index,scheduled):
                entered=time.monotonic();status=0;error=None;server=None
                try:
                    async with limit:
                        c,course=clients[index%30];response=await c.get('/api/web/v1/courses/'+course)
                        status=response.status_code
                        if 'x-pr03b-diagnostic-seconds' in response.headers:server=float(response.headers['x-pr03b-diagnostic-seconds'])
                except Exception as e:error=type(e).__name__
                end=time.monotonic();row=dict(phase=phase,kind='business',index=index,op=str(uuid.uuid4()),expected_status=200,status=status,error=error,start_monotonic=entered,finish_monotonic=end,seconds=end-entered,since_scheduled_seconds=end-scheduled,scheduling_lag_seconds=entered-scheduled,server_seconds=server)
                raw.write(json.dumps(row)+'\n')
            counter=0
            while time.monotonic()<start+duration:
                elapsed=time.monotonic()-start
                while counter/10<=elapsed and counter/10<duration:
                    if len(pending)>=256:raise RuntimeError('business client admission exhausted')
                    task=asyncio.create_task(sample(counter,start+counter/10));pending.add(task);task.add_done_callback(pending.discard);counter+=1
                await asyncio.sleep(.005)
            if pending:await asyncio.gather(*pending)
            raw.flush();print(json.dumps(dict(phase=phase,finished=True,scheduled=counter)),flush=True)
    finally:
        for c,_ in clients:await c.aclose()
try:asyncio.run(run())
except BaseException as e:
    print(json.dumps(dict(error_kind=type(e).__name__)),file=sys.stderr);raise SystemExit(1) from None
finally:raw.close()
