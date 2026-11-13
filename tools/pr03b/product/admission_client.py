"""Bounded R02/R04 process-count/burst probe; no full-capacity approval."""
import asyncio,json,os,pathlib,ssl,sys,time,uuid
import httpx

def evaluate(rows,metrics,api_count):
    events=sorted(metrics,key=lambda r:r['time']);active=waiting=active_peak=waiting_peak=0;valid=True
    for event in events:
        kind=event['stage']
        if kind=='active_enter':active+=1
        elif kind=='active_leave':active-=1
        elif kind=='wait_enter':waiting+=1
        elif kind=='wait_leave':waiting-=1
        valid=valid and 0<=active<=2 and 0<=waiting<=8
        active_peak=max(active_peak,active);waiting_peak=max(waiting_peak,waiting)
    rejected=[];checks=[];groups={}
    for burst in (16,32):
        samples=[r for r in rows if r['burst']==burst and r['kind']=='burst'];assert len(samples)==burst
        for sample in samples:
            correlated=[m for m in events if m['op']=='r04_'+sample['op']]
            sample['actual_hash_calls']=sum(m['stage']=='hash_compute' for m in correlated)
            sample['queue_full']=any(m['stage']=='queue_full' for m in correlated)
            checks.append(sample['error'] is None and sample['status'] in (200,429))
            checks.append(sample['actual_hash_calls']==(1 if sample['status']==200 else 0))
            if sample['queue_full']:
                rejected.append(sample);checks.append(sample['status']==429 and sample['seconds']<=.5)
        groups[str(burst)]=dict(requests=burst,successes=sum(r['status']==200 for r in samples),rejections=sum(r['status']==429 for r in samples),
            full_queue=sum(r['queue_full'] for r in samples),errors=sum(r['error'] is not None for r in samples))
        checks.append(any(r['queue_full'] for r in samples))
        controls=[r for r in rows if r['burst']==burst and r['kind']=='recovery']
        checks.append(len(controls)==1 and controls[0]['status']==200 and controls[0]['error'] is None)
    pids={m['pid'] for m in events if m['stage']=='hash_compute'}
    checks.extend([valid,active==waiting==0,active_peak==2,len(pids)==api_count])
    return dict(passed=all(checks),checks=len(checks),api_processes_with_real_hashes=len(pids),active_peak=active_peak,waiting_peak=waiting_peak,
        active_final=active,waiting_final=waiting,groups=groups,full_queue_max_seconds=max((r['seconds'] for r in rejected),default=None),
        full_queue_count=len(rejected),tail_approval=False,capacity_acceptance=False,
        limitations=['Two finite bursts per process count, not the full 20-minute mixed/10-minute overload matrix','No p99/confidence approval from these sample counts','No active business worker coexistence or target VM'],all_observations_retained=True)

async def run(cfg):
    O=pathlib.Path(cfg['output']);isolation=json.loads((O/'RUN.json').read_text())
    assert os.geteuid()==1000 and os.readlink('/proc/self/ns/net')==isolation['network_namespace']!=isolation['host_network_namespace']
    assert cfg['mode']=='r04_probe' and not isolation['routes'] and all(i['ifname']=='lo' for i in isolation['interfaces'])
    context=ssl.create_default_context(cafile=str(O/'root-ca.pem'));rows=[];journal=(O/'BURST-REQUESTS.jsonl').open('x',buffering=1)
    (O/'BURST-PREREGISTRATION.json').write_text(json.dumps(dict(api_processes=len(cfg['api_ports']),bursts=[16,32],hash_slots=2,waiting_slots=8,
        queue_seconds=1,full_queue_seconds=.5,capacity_acceptance=False,production_limits_changed=False),indent=2)+'\n')
    try:
        for burst in (16,32):
            clients=[]
            try:
                for index in range(burst):
                    origin=f"https://127.0.0.1:{cfg['api_ports'][index%len(cfg['api_ports'])]}"
                    client=httpx.AsyncClient(base_url=origin,verify=context,trust_env=False,timeout=5,follow_redirects=False)
                    clients.append((client,origin,index))
                async def preauth(client,origin,index):
                    response=await client.get('/api/web/v1/auth/csrf');assert response.status_code==200
                    return client,origin,index,response.json()['csrf_token']
                ready=await asyncio.gather(*(preauth(*r) for r in clients));gate=asyncio.Event()
                async def login(item,kind):
                    client,origin,index,csrf=item;await gate.wait();started=time.monotonic();op=str(uuid.uuid4());status=None;error=None
                    try:
                        async with asyncio.timeout(5):
                            response=await client.post('/api/web/v1/auth/login',json=dict(email=cfg['users'][index%30]['email'],password='Synthetic-capacity-only-20261110',remember_me=False,device_name='R04 synthetic burst'),
                                headers={'Origin':origin,'Sec-Fetch-Site':'same-origin','X-CSRF-Token':csrf,'Idempotency-Key':op})
                            status=response.status_code
                    except Exception as exc:error=type(exc).__name__
                    row=dict(burst=burst,kind=kind,index=index,api_index=index%len(cfg['api_ports']),op=op,status=status,error=error,seconds=time.monotonic()-started)
                    rows.append(row);journal.write(json.dumps(row)+'\n')
                tasks=[asyncio.create_task(login(item,'burst')) for item in ready];gate.set();await asyncio.gather(*tasks)
                # New public preauth after all replies; no write is retried.
                client,origin,index=clients[0]
                client.cookies.clear();response=await client.get('/api/web/v1/auth/csrf');assert response.status_code==200
                await login((client,origin,index,response.json()['csrf_token']),'recovery')
            finally:
                for client,_,_ in clients:await client.aclose()
        metrics=[json.loads(line) for p in O.glob('service-metrics-*.jsonl') for line in p.read_text().splitlines()]
        report=evaluate(rows,metrics,len(cfg['api_ports']))
        (O/'BURST-ROWS.json').write_text(json.dumps(rows,indent=2)+'\n');(O/'BURST-RESULT.json').write_text(json.dumps(report,indent=2)+'\n')
        return report['passed']
    finally:journal.close()

if __name__=='__main__':raise SystemExit(0 if asyncio.run(run(json.loads(sys.stdin.readline()))) else 1)
