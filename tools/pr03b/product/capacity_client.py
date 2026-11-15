"""Open-loop same-NAT workload; every denial, timeout and scheduling miss kept."""
import asyncio,json,os,pathlib,ssl,subprocess,sys,time,uuid,statistics
cfg=json.loads(sys.stdin.readline());W=pathlib.Path(cfg['work']);R=W.parents[2];O=pathlib.Path(cfg['output']);RAM=pathlib.Path(cfg['ram'])
import httpx,websockets
ctx=ssl.create_default_context(cafile=str(O/'root-ca.pem'))
password='Synthetic-capacity-only-20261110';BASE='/api/web/v1';raw=(O/'REQUESTS.jsonl').open('w',buffering=1)
rows=[];sockets=[];clients=[];failures=[]
stage='setup';business_process=None
headers=lambda origin,csrf:{'Origin':origin,'Sec-Fetch-Site':'same-origin','X-CSRF-Token':csrf,'Idempotency-Key':str(uuid.uuid4())}

async def run():
    global stage,business_process
    for i,u in enumerate(cfg['users']):
        origin=f"https://127.0.0.1:{cfg['api_ports'][i%2]}";client=httpx.AsyncClient(base_url=origin,verify=ctx,trust_env=False,timeout=6,limits=httpx.Limits(max_connections=32,max_keepalive_connections=8))
        stage='setup_csrf_'+str(i);csrf=(await client.get(BASE+'/auth/csrf')).json()['csrf_token']
        stage='setup_login_'+str(i);login=await client.post(BASE+'/auth/login',json=dict(email=u['email'],password=password,remember_me=False,device_name='Synthetic capacity client'),headers=headers(origin,csrf))
        if login.status_code!=200 or login.json()['state']!='authenticated':raise RuntimeError('setup login failed')
        csrf=login.json()['csrf_token'];clients.append((client,origin,csrf,u))
        ticket=await client.post(BASE+'/ws-tickets',json=dict(channel='events',class_session_id=None,capture_epoch_id=None,fence_token=None),headers=headers(origin,csrf))
        if ticket.status_code!=201:raise RuntimeError('setup ticket failed')
        stage='setup_WS_'+str(i);ws=await websockets.connect(origin.replace('https:','wss:')+BASE+'/ws/events',ssl=ctx,origin=origin,subprotocols=['classroom.v1'],additional_headers={'Cookie':'__Host-classroom_session='+client.cookies.get('__Host-classroom_session')},max_size=4096)
        await ws.send(json.dumps(dict(type='authenticate',ticket=ticket.json()['ticket'])));sockets.append(ws)
    await asyncio.sleep(1)
    if any(ws.state.name!='OPEN' for ws in sockets):raise RuntimeError('setup WS authentication failed')
    # Actual Chromium chain validation with a task-private NSS home. This
    # optional browser check does not run concurrently with measured phases.
    bcfg=dict(origin=clients[0][1],email=clients[0][3]['email'],password=password,retryCredentials=cfg['mode']=='browser',playwrightModule=str(R/'web/frontend/node_modules/@playwright/test/index.mjs'),
        screenshot=str(O/'real-Chromium.png'),chromium=str(W/'linux-browser/chromium-1248/chrome-linux64/chrome'),work=str(W),output=str(O),ram=str(RAM),harness=cfg['harness'])
    stage='strict_browser';browser=await asyncio.create_subprocess_exec('unshare','--user','--map-root-user','--mount','--fork',sys.executable,'-I','-B',str(pathlib.Path(cfg['harness'])/'browser_namespace.py'),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    stdout,stderr=await asyncio.wait_for(browser.communicate(json.dumps(bcfg).encode()),timeout=120)
    (O/'BROWSER.json').write_bytes(stdout)
    # Only fixed error classes/positions are exported by the Node harness.
    (O/'browser-launch.log').write_text('exit_code='+str(browser.returncode)+'\n')
    if browser.returncode!=0:raise RuntimeError('strict browser verification failed')
    async def business_ack():
        try:return await asyncio.wait_for(asyncio.to_thread(business_process.stdout.readline),timeout=20)
        except asyncio.TimeoutError:
            business_process.terminate();business_process.wait(10)
            raise
    contexts=[dict(origin=origin,course_id=u['course_id'],session=c.cookies.get('__Host-classroom_session')) for c,origin,csrf,u in clients]
    business_process=subprocess.Popen([sys.executable,'-I','-B',str(pathlib.Path(cfg['harness'])/'business_client.py')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=(O/'business-client.log').open('wb'),text=True)
    business_process.stdin.write(json.dumps(dict(output=str(O),contexts=contexts))+'\n');business_process.stdin.flush()
    ready=json.loads(await business_ack());assert ready.get('ready') is True
    durations=(('baseline',120),('normal',120)) if cfg['mode']=='timing' else () if cfg['mode']=='browser' else (('baseline',10),('normal',20),('overload',20),('recovery',10)) if cfg['mode']=='smoke' else (('baseline',120),('normal',1200),('overload',600),('recovery',180))
    sequence=0;pending=set();client_limit=asyncio.Semaphore(256)
    async def sample(phase,kind,index,scheduled):
        start=time.monotonic();key=str(uuid.uuid4());client,origin,csrf,u=clients[index%30];status=0;error=None;expected=200;server_seconds=None
        try:
            async with client_limit:
                if kind=='business':response=await client.get(BASE+'/courses/'+u['course_id'])
                elif kind=='login':
                    # A fresh preauth for every full login; never clear a real
                    # failure budget or substitute source/account mid-sequence.
                    async with httpx.AsyncClient(base_url=origin,verify=ctx,trust_env=False,timeout=6) as attempt:
                        pre=await attempt.get(BASE+'/auth/csrf')
                        if pre.status_code!=200:response=pre
                        else:
                            body=dict(email=u['email'],password=password,remember_me=False,device_name='Synthetic measured login')
                            # Separate known-wrong and missing-account arms.
                            # At 1/40 total attempts per arm this preserves the
                            # actual hard 3/900s and shared same-NAT budget.
                            if index%40==19:body['email']=cfg['users'][(index//40)%30]['email'];body['password']='synthetic-deliberately-wrong';expected=401
                            if index%40==39:body['email']='missing-'+str((index//40)%30)+'@example.test';expected=401
                            response=await attempt.post(BASE+'/auth/login',json=body,headers=headers(origin,pre.json()['csrf_token']))
                else:
                    operation=dict(purpose='changePassword',target=dict(kind='account',id=u['id']),candidate_parameters=dict(idempotency_key=key,body=dict(new_password='Synthetic-new-password-not-committed')))
                    response=await client.post(BASE+'/auth/reauth',json=dict(password=password,operation=operation),headers=headers(origin,csrf))
                status=response.status_code;server_seconds=float(response.headers['x-pr03b-diagnostic-seconds']) if 'x-pr03b-diagnostic-seconds' in response.headers else None
                if kind!='overload_hash' and status!=expected:failures.append(dict(phase=phase,kind=kind,status=status,expected=expected))
        except Exception as e:error=type(e).__name__;failures.append(dict(phase=phase,kind=kind,error=error))
        row=dict(server_seconds=server_seconds,phase=phase,kind=kind,index=index,op=key,expected_status=expected,status=status,error=error,start_monotonic=start,finish_monotonic=time.monotonic(),seconds=time.monotonic()-start,
            since_scheduled_seconds=time.monotonic()-scheduled,scheduling_lag_seconds=start-scheduled)
        rows.append(row);raw.write(json.dumps(row)+'\n')
    phase_times=[]
    for phase,duration in durations:
        (O/'PHASE.json').write_text(json.dumps(dict(phase=phase,seconds=duration)))
        start=time.monotonic()+.05;next_business=0;next_login=0;next_hash=0
        phase_times.append(dict(phase=phase,start=start,end=start+duration))
        business_process.stdin.write(json.dumps(dict(phase=phase,start=start,duration=duration))+'\n');business_process.stdin.flush()
        while time.monotonic()<start+duration:
            elapsed=time.monotonic()-start
            for kind,rate in [('login',0 if phase=='baseline' else 1),('overload_hash',32 if phase=='overload' else 0)]:
                if not rate:continue
                counter={'business':next_business,'login':next_login,'overload_hash':next_hash}[kind]
                while counter/rate<=elapsed and counter/rate<duration:
                    scheduled=start+counter/rate
                    if len(pending)>=256:
                        row=dict(server_seconds=server_seconds,phase=phase,kind=kind,status=0,error='GeneratorAdmissionFull',seconds=0,since_scheduled_seconds=time.monotonic()-scheduled,scheduling_lag_seconds=time.monotonic()-scheduled)
                        rows.append(row);raw.write(json.dumps(row)+'\n');failures.append(dict(phase=phase,kind=kind,error='GeneratorAdmissionFull'))
                    else:
                        task=asyncio.create_task(sample(phase,kind,counter,scheduled));pending.add(task);task.add_done_callback(pending.discard)
                    counter+=1
                if kind=='business':next_business=counter
                elif kind=='login':next_login=counter
                else:next_hash=counter
            if any(ws.state.name!='OPEN' for ws in sockets):failures.append(dict(phase=phase,error='UnexpectedWebSocketClosed'))
            await asyncio.sleep(.005)
        if pending:await asyncio.gather(*list(pending))
        done=json.loads(await business_ack());assert done.get('finished') and done['phase']==phase and done['scheduled']==duration*10
        for line in (O/'BUSINESS-REQUESTS.jsonl').read_text().splitlines():
            row=json.loads(line)
            if row['phase']==phase:
                rows.append(row);raw.write(json.dumps(row)+'\n')
                if row['status']!=200 or row['error'] is not None:failures.append(dict(phase=phase,kind='business',status=row['status'],error=row['error']))

        print(json.dumps(dict(phase=phase,observations=sum(r['phase']==phase for r in rows))),flush=True)
    (O/'PHASE.json').write_text(json.dumps(dict(phase='finished')))
    (O/'PHASE_TIMES.json').write_text(json.dumps(phase_times,indent=2)+'\n')
    def quantile(values,p):
        values=sorted(values);return values[min(len(values)-1,max(0,int((len(values)-1)*p)))] if values else None
    summary=[]
    for phase,_ in durations:
        for kind in ('business','login','overload_hash'):
            for expected in (200,401):
                group=[r for r in rows if r['phase']==phase and r['kind']==kind and r.get('expected_status')==expected]
                if not group:continue
                good=[r['seconds'] for r in group if r['status']==expected];all_times=[r['since_scheduled_seconds'] for r in group]
                summary.append(dict(phase=phase,kind=kind,expected_status=expected,N=len(group),N_expected=len(good),status_counts={str(s):sum(r['status']==s for r in group) for s in set(r['status'] for r in group)},
                    expected_p50=quantile(good,.5),expected_p95=quantile(good,.95),expected_p99=quantile(good,.99),p99_sample_gate=len(good)>=1000,
                    all_outcome_scheduled_p95=quantile(all_times,.95),all_outcome_scheduled_p99=quantile(all_times,.99)))
    (O/'PERFORMANCE.json').write_text(json.dumps(dict(mode=cfg['mode'],phases=dict(durations),quantile_method='sorted floor((N-1)*p); no failed samples removed from raw observations',groups=summary,
        failures=failures[:100],failure_count=len(failures),websockets=30,same_nat=True,thresholds_unchanged=True,smoke_is_acceptance=False),indent=2)+'\n')
    print(json.dumps(dict(finished=True,failure_count=len(failures))),flush=True)
    for ws in sockets:await ws.close()
    for client,_,_,_ in clients:await client.aclose()

try:asyncio.run(run())
except BaseException as error:
    import traceback
    causes=[];current=error
    while current and len(causes)<8:
        causes.append(dict(kind=type(current).__name__,verify_code=getattr(current,'verify_code',None),errno=getattr(current,'errno',None)));current=current.__cause__
    (O/'CLIENT-ERROR.json').write_text(json.dumps(dict(error_kind=type(error).__name__,stage=stage,causes=causes,frames=[dict(function=f.name,line=f.lineno) for f in traceback.extract_tb(error.__traceback__)[-5:]]))+'\n');raise SystemExit(1) from None
finally:
    raw.close()
    if business_process:
        try:
            business_process.stdin.write(json.dumps(dict(stop=True))+'\n');business_process.stdin.flush();business_process.wait(10)
        except (BrokenPipeError,subprocess.TimeoutExpired):business_process.terminate();business_process.wait(10)
        if business_process.returncode!=0:raise SystemExit(1)

    # asyncio.run closes transports, and the supervisor separately drains SUT.
