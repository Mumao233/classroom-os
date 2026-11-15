"""Functional R05 renewal proof, explicitly not a statistical campaign."""
import asyncio,json,os,pathlib,ssl,sys,uuid
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import httpx
from enumeration_trial import collect
import enumeration_renewal as method

cfg=json.loads(sys.stdin.readline());O=pathlib.Path(cfg['output'])
assert cfg['mode']=='r05_probe' and os.geteuid()==1000
assert pathlib.Path(cfg['ram']).is_dir() and len(cfg['users'])==30
isolation=json.loads((O/'RUN.json').read_text())
assert os.readlink('/proc/self/ns/net')==isolation['network_namespace']!=isolation['host_network_namespace']
assert not isolation['routes'] and all(i['ifname']=='lo' for i in isolation['interfaces'])
design=method.plan(20261111);origins=[f'https://127.0.0.1:{p}' for p in cfg['api_ports']]
assert len(origins)==2
context=ssl.create_default_context(cafile=str(O/'root-ca.pem'))
(O/'R05-PREREGISTRATION.json').write_text(json.dumps(dict(design=design,commitment=method.commitment(design),
    purpose='functional_validation_only',statistical_acceptance=False,planned_arms=4,scheduled_login_requests=32,
    capacity_prerequisite_bypassed_for_acceptance=False),indent=2)+'\n')
journal=(O/'R05-EVENTS.jsonl').open('x',buffering=1);rows=[]

async def run():
    for schedule in ('quota','natural_expiry'):
        (O/'PHASE.json').write_text(json.dumps(dict(phase='r05_'+schedule,seconds=7 if schedule=='quota' else 906)))
        async def arm_run(arm):
            identity=str(uuid.uuid4());index=0 if schedule=='quota' else 1
            email=cfg['users'][index]['email'] if arm=='known' else 'missing-'+identity+'@example.test'
            async with httpx.AsyncClient(verify=context,trust_env=False,timeout=5,follow_redirects=False) as client:
                def emit(event):journal.write(json.dumps(dict(trial=identity,arm=arm,schedule=schedule,**event))+'\n')
                row=await collect(client,origin=origins[0],origins=origins,email=email,password='intentionally-wrong-R05-synthetic',
                    target=identity,trial=identity,split='functional',arm=arm,cell=0,schedule=schedule,design=design,emit=emit)
                rows.append(row);(O/'R05-ROWS.json').write_text(json.dumps(rows,indent=2)+'\n')
        await asyncio.gather(*(arm_run(a) for a in ('known','unknown')))

passed=False
try:
    asyncio.run(run())
    metrics=[json.loads(line) for p in O.glob('service-metrics-*.jsonl') for line in p.read_text().splitlines()]
    for row in rows:
        for sample in row['sequence']:
            hits=[r for r in metrics if r['stage']=='hash_compute' and r['op']=='r05_'+str(sample['op'])]
            sample['expensive_calls']=len(hits);sample['hash_pids']=sorted({r['pid'] for r in hits})
    method.validate_units([{**r,'split':'calibration'} for r in rows],design)
    assertions=[]
    for row in rows:
        expected=[401]*3+[429]*5 if row['schedule']=='quota' else [401]*6+[429]*2
        assertions.append(row['complete'] and row['context_count']==2 and [v['status'] for v in row['sequence']]==expected)
        assertions.append(all(v['expensive_calls']==(1 if status==401 else 0) for v,status in zip(row['sequence'],expected)))
        assertions.append(len({pid for v in row['sequence'] for pid in v['hash_pids']})==2)
        if row['schedule']=='natural_expiry':assertions.append(row['sequence'][3]['start_seconds']>=902 and row['protocol'][1]['wait_seconds']>=895)
    passed=all(assertions) and len(rows)==4
    (O/'R05-ROWS.json').write_text(json.dumps(rows,indent=2)+'\n')
    readiness=method.readiness(design,None,collector_verified=False,continuous_control_verified=passed)
    (O/'R05-FUNCTIONAL.json').write_text(json.dumps(dict(passed=passed,assertions=len(assertions),arms=len(rows),
        statuses={r['schedule']+'_'+r['arm']:[v['status'] for v in r['sequence']] for r in rows},
        actual_elapsed_seconds={r['schedule']+'_'+r['arm']:r['elapsed_seconds'] for r in rows},
        actual_hash_calls={r['schedule']+'_'+r['arm']:sum(v['expensive_calls'] for v in r['sequence']) for r in rows},
        no_enumeration_PASS=False,readiness=readiness,statistical_sample_counts_zero=True,production_limits_changed=False,
        secrets_exported=False),indent=2)+'\n')
finally:journal.close()
raise SystemExit(0 if passed else 1)
