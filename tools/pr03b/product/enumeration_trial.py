"""One pre-registered same-target public trial; no SQL, budget reset or retry.

Transport/clock injection supports fault tests. The live supervisor supplies only
its owned isolated HTTPS origin. No cookie, CSRF value or password is exported.
"""
import asyncio,math,re,time,uuid
from urllib.parse import urlsplit
from enumeration_renewal import SCHEDULES,commitment

async def collect(client,*,origin,email,password,target,trial,split,arm,cell,schedule,design,
                  origins=None,clock=time.monotonic,sleep=asyncio.sleep,emit=lambda row:None):
    origins=origins or [origin]
    for endpoint in origins:
        parsed=urlsplit(endpoint)
        if parsed.scheme!='https' or parsed.hostname!='127.0.0.1' or not parsed.port or parsed.port==5432 or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('owned literal-loopback HTTPS origin required')
    if not email.endswith('.test') or arm not in ('known','unknown') or split not in ('calibration','holdout','functional') or schedule not in SCHEDULES:
        raise ValueError('synthetic trial metadata required')
    if design.get('schema_version')!=3 or design.get('sample_reduction_allowed') is not False:raise ValueError('approved method required')
    start=clock();sequence=[];protocol=[];csrf=None;last_finish=0.;failure=None
    row=dict(trial=trial,arm=arm,split=split,cell=cell,target=target,time_block=trial,schedule=schedule,
        context_count=0,budget_resets=0,target_changes=0,application_restarts=0,source_changes=0,
        plan_commitment=commitment(design),sequence=sequence,protocol=protocol)
    async def wait_until(offset):
        while clock()-start<offset:
            await sleep(min(1,offset-(clock()-start)))
            if clock()-start>design['max_trial_seconds']:raise TimeoutError('trial deadline')
    async def exchange(kind,index,scheduled):
        nonlocal csrf,last_finish
        entered=clock()-start;op=str(uuid.uuid4());status=None;length=0;code=None;timeout=False;error=None
        endpoint=origins[index%len(origins)]
        try:
            async with asyncio.timeout(5):
                if kind=='csrf':response=await client.get(endpoint+'/api/web/v1/auth/csrf',headers={'Cache-Control':'no-store'})
                else:response=await client.post(endpoint+'/api/web/v1/auth/login',json=dict(email=email,password=password,remember_me=False,device_name='R05 synthetic trial'),
                    headers={'Origin':endpoint,'Sec-Fetch-Site':'same-origin','X-CSRF-Token':csrf,'Idempotency-Key':op})
                status=response.status_code;length=len(response.content);value=response.json()
                if status>=400:code=value.get('error',{}).get('code')
                if kind=='csrf' and status==200:
                    value=value.get('csrf_token')
                    if not isinstance(value,str) or not re.fullmatch('[A-Za-z0-9_-]{43}',value):raise ValueError('invalid CSRF response')
                    csrf=value
        except (TimeoutError,asyncio.TimeoutError):timeout=True;error='TimeoutError'
        except Exception as exc:error=type(exc).__name__
        finish=clock()-start
        event=dict(kind=kind,index=index,before_index=index,scheduled_seconds=scheduled,start_seconds=entered,finish_seconds=finish,
            wait_seconds=max(0,entered-last_finish),ms=(finish-entered)*1000,status=status,length=length,code=code,timeout=timeout,
            error=error,op=op,target=target,api_index=index%len(origins))
        last_finish=finish;emit(event)
        if kind=='csrf':protocol.append(event);row['context_count']+=1
        else:sequence.append(event)
        if error or (kind=='csrf' and status!=200):raise RuntimeError('public protocol observation failed')
        if kind=='login' and status not in (401,429):raise RuntimeError('unexpected public authentication result')
        return event
    try:
        await exchange('csrf',0,0)
        for index,offset in enumerate(SCHEDULES[schedule]):
            await wait_until(offset)
            if index==3:
                if schedule=='quota':client.cookies.clear()
                await exchange('csrf',index,offset)
            await exchange('login',index,offset)
    except Exception as exc:failure=type(exc).__name__
    finally:
        # Preserve partial sequences and all missing planned observations.
        for index in range(len(sequence),8):
            offset=SCHEDULES[schedule][index]
            event=dict(kind='login',index=index,before_index=index,scheduled_seconds=offset,start_seconds=None,finish_seconds=None,wait_seconds=0,
                ms=None,status=None,length=0,code=None,timeout=False,error='NotObtained',op=None,target=target,api_index=None)
            sequence.append(event);emit(event)
        row.update(elapsed_seconds=clock()-start,complete=failure is None,failure=failure)
    assert math.isfinite(row['elapsed_seconds'])
    return row
