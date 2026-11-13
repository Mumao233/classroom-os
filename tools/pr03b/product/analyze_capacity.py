"""Analyze every recorded outcome; never rewrites raw observations or thresholds."""
import json,math,sys
from pathlib import Path
from collections import Counter
R=Path(__file__).resolve().parents[3];O=R/'outputs/pr03b/product-completion-20261110-01'
p=Path(sys.argv[1]).resolve();assert p.parent==O.resolve() and p.name.startswith('capacity-')
def read(name):return json.loads((p/name).read_text())
def lines(name):return [json.loads(s) for s in (p/name).read_text().splitlines() if s]
def q(v,f):
    v=sorted(v);return v[int((len(v)-1)*f)] if v else None
def stats(v):return dict(N=len(v),p50=q(v,.5),p95=q(v,.95),p99=q(v,.99),p99_sample_gate=len(v)>=1000)
rows=lines('REQUESTS.jsonl');phases={x['phase']:x for x in read('PHASE_TIMES.json')};final=read('FINAL.json')
metrics=sorted([m for f in p.glob('service-metrics-*.jsonl') for m in lines(f.name)],key=lambda m:m['time'])
resources=lines('RESOURCES.jsonl');groups={}
for phase in phases:
    selected=[r for r in rows if r['phase']==phase]
    groups[phase]={}
    for kind in ('business','login','overload_hash'):
        for arm in ('correct','wrong','dummy') if kind=='login' else ('all',):
            sample=[r for r in selected if r['kind']==kind and (kind!='login' or ('wrong' if r['index']%40==19 else 'dummy' if r['index']%40==39 else 'correct')==arm)]
            if sample:groups[phase][kind+'_'+arm]=dict(all_outcomes=stats([r['seconds'] for r in sample]),status_counts=dict(Counter(str(r['status']) for r in sample)),errors=sum(r['error'] is not None for r in sample),unexpected=sum(r['status']!=r['expected_status'] or r['error'] is not None for r in sample),scheduled=stats([r['since_scheduled_seconds'] for r in sample]))
hash_groups={}
for phase,t in phases.items():
    for arm in ('login_correct','login_wrong','login_dummy','login'):
        values=[m['seconds'] for m in metrics if m['stage']=='hash_compute' and m['op']==arm and t['start']<=m['time']<t['end']]
        if values:hash_groups[phase+'_'+arm]=stats(values)
byop={}
for m in metrics:
    if m['op'] and not m['op'].startswith('login'):byop.setdefault(m['op'],[]).append(m)
rejections={}
for stage in ('queue_full','queue_timeout'):
    ops={op for op,events in byop.items() if any(m['stage']==stage for m in events)}
    matched=[r for r in rows if r['op'] in ops]
    rejections[stage]=dict(N_metrics=len(ops),N_http=len(matched),http=stats([r['seconds'] for r in matched]),admission=stats([m['seconds'] for m in metrics if m['stage']==stage]),status_counts=dict(Counter(str(r['status']) for r in matched)),actual_hash_count=sum(m['stage']=='hash_compute' for op in ops for m in byop[op]))
peak=max(int(v['memory.peak']) for r in resources for k,v in r.items() if k.endswith('-sut'))
oom=max(int(line.split()[1]) for r in resources for k,v in r.items() if k.endswith('-sut') for line in v['memory.events'].splitlines() if line.split()[0] in ('oom','oom_kill'))
occupancy={};maxima={}
for kind in ('active','wait'):
    count=0;maximum=0;trajectory=[]
    for m in metrics:
        if m['stage']==kind+'_enter':count+=1
        elif m['stage']==kind+'_leave':count-=1
        else:continue
        maximum=max(maximum,count);trajectory.append((m['time'],count))
    maxima[kind]=dict(maximum=maximum,final=count,events=len(trajectory));occupancy[kind]=trajectory
recovery=None
if occupancy['wait']:
    t=phases['recovery'];trajectory=occupancy['wait'];cursor=0;count=0;samples=[]
    for i in range(math.ceil((t['end']-t['start'])*10)):
        at=t['start']+i/10
        while cursor<len(trajectory) and trajectory[cursor][0]<=at:count=trajectory[cursor][1];cursor+=1
        samples.append(count)
    zero=next((i for i in range(len(samples)-99) if max(samples[i:i+100])==0),None)
    end_rows=[r for r in rows if r['phase']=='recovery' and r.get('start_monotonic',0)>=t['end']-120]
    recovery=dict(first_continuous_10_seconds_zero_queue_after_seconds=None if zero is None else zero/10,last_120_seconds_N=len(end_rows),last_120_seconds_unexpected=sum(r['error'] is not None or r['status']!=r['expected_status'] for r in end_rows),last_120_seconds_groups={kind:stats([r['seconds'] for r in end_rows if r['kind']==kind]) for kind in ('business','login')})
    # The phase begins after outstanding client responses drain. Measure the
    # actual queue recovery from the planned overload cutoff as well, using
    # every entry/leave event so short waits cannot disappear between samples.
    cutoff=phases['overload']['end'];count=0
    for at,n in trajectory:
        if at<=cutoff:count=n
        else:break
    zero_since=cutoff if count==0 else None;confirmed=None
    for at,n in [*[(at,n) for at,n in trajectory if cutoff<at<=t['end']],(t['end'],None)]:
        if zero_since is not None and at-zero_since>=10:confirmed=zero_since-cutoff;break
        if n is None:break
        count=n
        if count==0:
            if zero_since is None:zero_since=at
        else:zero_since=None
    recovery.update(sampled_window_origin='recovery_phase_start',phase_gap_after_overload_end_seconds=t['start']-cutoff,
        exact_zero_queue_window_start_after_overload_end_seconds=confirmed,
        exact_zero_queue_window_confirmed_after_overload_end_seconds=None if confirmed is None else confirmed+10)
    recovery['last_120_seconds_login_arms']={arm:stats([r['seconds'] for r in end_rows if r['kind']=='login' and ('wrong' if r['index']%40==19 else 'dummy' if r['index']%40==39 else 'correct')==arm]) for arm in ('correct','wrong','dummy')}
baseline=groups['baseline']['business_all']['all_outcomes']['p95'];normal=groups['normal']['business_all']['all_outcomes']['p95']
report=dict(run=final['run'],python=final.get('python_version','3.14.4 (outside declared support; screening only)'),all_outcomes_retained=True,groups=groups,hash_compute=hash_groups,rejections=rejections,occupancy=maxima,recovery=recovery,memory=dict(peak_bytes=peak,effective_limit_bytes=4*1024**3,headroom_fraction=1-peak/(4*1024**3),oom_events=oom),normal_business_p95_relative_deterioration=normal/baseline-1,cleanup=dict(client_exit=final['client_exit_code'],server_exit=final['bootstrap_exit_code'],cgroups_empty=all(final['cgroups_empty'].values()),ram_unmounted=final['ram_unmounted']),limits=['Same-host WSL2 namespaces, not independently provisioned target VM','No active business worker coexistence or API process-count variants','No complete candidate/concurrency/password/cold-hot strata or 16/32 burst matrix','Wrong/dummy strata below N=1000; no p99 approval for those strata','No inferential tail confidence interval; point estimates only','Not R05 enumeration evidence; real hard budgets retained','Not independent S4 approval; real identity/D0 and retention remain external'])
(p/'ANALYSIS.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(dict(run=report['run'],memory=report['memory'],business_deterioration=report['normal_business_p95_relative_deterioration'],rejections=rejections,occupancy=maxima,recovery=recovery),indent=2))
