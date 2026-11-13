"""R05 public-context renewal method approved by the user on 2026-11-11.

Pure plan/analysis only. Historical schema 2 and its closed collector stay intact.
"""
import copy,hashlib,json,math,statistics
import enumeration_plan as legacy
import evidence_stats as es

SCHEDULES={'quota':list(range(8)),'natural_expiry':[0,1,2,902,903,904,905,906]}
VIEWS=(('full_sequence','quota',(1,2,4,8)),('admitted','natural_expiry',(1,2,4)),
       ('renewed_full_sequence','natural_expiry',(1,2,4,8)))
AGGREGATES=(*legacy.AGGREGATES,'elapsed','public_transcript')

def plan(seed):
    result=copy.deepcopy(legacy.plan(seed))
    result.update(schema_version=3,method='public_renewal_natural_expiry_20261111',
        approval='User explicitly approved public preauth renewal and natural budget expiry; runtime limits and acceptance thresholds unchanged',
        context='Public GET /auth/csrf may renew expired preauth; quota control may discard browser cookies before renewal. Same target/source/application and uninterrupted server budgets throughout each sequence.',
        schedules=SCHEDULES,views=[dict(name=n,schedule=s,ks=k) for n,s,k in VIEWS],aggregates=AGGREGATES,
        family_size=len(result['cells'])*sum(len(k) for _,_,k in VIEWS)*len(AGGREGATES),
        account_failure_limit=3,account_window_seconds=900,preauth_seconds=300,
        renewal=dict(quota_before_index=3,natural_expiry_before_index=3,protocol_deadline_seconds=5),
        observations='Keep every login, CSRF response, actual wait, denial, timeout and missing scheduled observation; never subtract waiting from elapsed/transcript scores.',
        independence='Both schedules require distinct complete trials/targets/time blocks; all prefixes of one sequence remain in its original split.',
        sample_reduction_allowed=False)
    return result

def commitment(design):return hashlib.sha256(json.dumps(design,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def readiness(design,capacity,*,collector_verified=False,continuous_control_verified=False):
    reasons=[]
    if not capacity or capacity.get('result')!='PASS':reasons.append('R02_R04_resource_and_capacity_prerequisite_not_PASS')
    if not collector_verified:reasons.append('complete_campaign_collector_not_verified')
    if not continuous_control_verified:reasons.append('continuous_cross_process_budget_control_not_verified')
    sequences=len(design['cells'])*len(design['splits'])*2*design['min_sequence_groups_per_cell_split_arm']*len(SCHEDULES)
    return dict(execution_ready=not reasons,result='READY' if not reasons else 'INCONCLUSIVE',reasons=reasons,
        minimum_independent_arm_sequences=sequences,minimum_scheduled_login_requests=sequences*8,
        natural_expiry_arm_sequences=sequences//2,minimum_natural_wait_arm_seconds=sequences//2*902,
        lower_bound_not_a_sufficient_sample_size=True,budget_or_threshold_modified=False,database_connections=0)

def resource_estimate(design,concurrent_natural_arms=30):
    assert isinstance(concurrent_natural_arms,int) and concurrent_natural_arms>0
    minimum=len(design['cells'])*len(design['splits'])*2*design['min_sequence_groups_per_cell_split_arm']
    # Even an observed AUC*=.5 needs this many independent holdout groups per
    # arm for THIS conservative simultaneous bound to have upper <=.55.
    needed=math.ceil(math.log(8*design['family_size']/.05)/(.55-.5)**2)
    best_case=len(design['cells'])*2*(design['min_sequence_groups_per_cell_split_arm']+max(needed,design['min_sequence_groups_per_cell_split_arm']))
    return dict(hypothetical_concurrent_natural_arms=concurrent_natural_arms,concurrency_not_validated=True,
        minimum_natural_arm_sequences=minimum,minimum_wait_arm_seconds=minimum*902,
        hypothetical_minimum_wait_days=minimum*902/concurrent_natural_arms/86400,
        best_point_AUC_star=.5,holdout_groups_per_cell_arm_needed_by_current_bound=needed,
        hypothetical_best_point_wait_days=best_case*902/concurrent_natural_arms/86400,
        excludes_bootstrap_protocol_hash_storage_queue_and_capacity_costs=True,not_a_runtime_prediction=True,
        thresholds_or_sample_minima_modified=False)

def validate_units(rows,design,*,functional=False):
    assert design['schema_version']==3
    legacy_rows=[]
    for row in rows:
        assert row['schedule'] in SCHEDULES and row['budget_resets']==0
        assert row['target_changes']==0 and row['application_restarts']==0 and row['source_changes']==0
        assert 1<=row['context_count']<=2
        assert len(row['sequence'])==8 and row['elapsed_seconds']<=design['max_trial_seconds']
        assert row['plan_commitment']==commitment(design)
        assert len(row['protocol'])==row['context_count']
        assert row['protocol'][0]['before_index']==0
        for i,sample in enumerate(row['sequence']):
            assert sample['index']==i and sample['scheduled_seconds']==SCHEDULES[row['schedule']][i]
            if sample['error']=='NotObtained':
                assert sample['start_seconds'] is None and sample['finish_seconds'] is None and sample['ms'] is None
                continue
            assert sample['start_seconds']>=sample['scheduled_seconds'] and sample['finish_seconds']>=sample['start_seconds']
            assert sample['wait_seconds']>=0 and sample['target']==row['target']
            assert sample['status'] is None or isinstance(sample['status'],int)
        if row['context_count']==2:
            assert row['protocol'][1]['before_index']==3 and row['protocol'][1]['start_seconds']>=SCHEDULES[row['schedule']][3]
        if row['schedule']=='natural_expiry' and any(s['status']==401 for s in row['sequence'][3:]):
            assert row['context_count']==2
        # Reuse the original split/trial/target/time-block uniqueness rules.
        assert row['split'] in design['splits'] or (functional and row['split']=='functional')
        legacy_rows.append({**row,'context_count':1,'split':'calibration' if functional and row['split']=='functional' else row['split']})
    return legacy.validate_units(legacy_rows,design)

def analyze(rows,design,controls=None):
    validate_units(rows,design);branches=[]
    for cell in design['cells']:
        for view,schedule,ks in VIEWS:
            for k in ks:
                selected=[r for r in rows if r['cell']==cell['id'] and r['schedule']==schedule and all(v['ms'] is not None for v in r['sequence'][:k]) and
                    (view!='admitted' or all(v['status']==401 and v.get('expensive_calls')==1 for v in r['sequence'][:k]))]
                for aggregate in AGGREGATES:
                    def score(row):
                        seq=row['sequence'][:k]
                        if aggregate=='status_length_sequence':return tuple((v['status'] if v['status'] is not None else -1,v['length'],v['code'] or '',v['timeout']) for v in seq)
                        if aggregate=='public_transcript':
                            events=[(v['before_index'],0,v['status'] if v['status'] is not None else -1,v['length'],v['code'] or '',v['timeout']) for v in row['protocol'] if v['before_index']<k]
                            events += [(v['index'],1,v['status'] if v['status'] is not None else -1,v['length'],v['code'] or '',v['timeout']) for v in seq]
                            return tuple(sorted(events))
                        if aggregate=='elapsed':return seq[-1]['finish_seconds']*1000
                        values=[v['ms'] for v in seq]
                        return statistics.mean(values) if aggregate=='mean' else statistics.median(values)
                    groups={split:{arm:[score(r) for r in selected if r['split']==split and r['arm']==arm] for arm in ('known','unknown')} for split in design['splits']}
                    counts={split:{arm:len(v) for arm,v in arms.items()} for split,arms in groups.items()}
                    branch=dict(cell=cell['id'],view=view,schedule=schedule,k=k,aggregate=aggregate,counts=counts,result='INCONCLUSIVE')
                    if min(n for arms in counts.values() for n in arms.values())==0:branches.append(branch);continue
                    frozen=es.fit(groups['calibration']['known'],groups['calibration']['unknown'])
                    result=es.evaluate(groups['holdout']['known'],groups['holdout']['unknown'],frozen,design['family_size'])
                    branch.update(frozen=frozen,**result)
                    if max(result['BA_95_simultaneous'][0],result['AUC_star_95_simultaneous'][0])>.55:branch['result']='FAIL'
                    elif min(n for arms in counts.values() for n in arms.values())>=1000 and max(result['BA_95_simultaneous'][1],result['AUC_star_95_simultaneous'][1])<=.55:
                        if aggregate in ('status_length_sequence','public_transcript'):
                            branch['result']='PASS' if set(groups['holdout']['known'])==set(groups['holdout']['unknown']) else 'FAIL'
                        else:
                            intervals=[]
                            for q in (.5,.95):
                                interval=es.quantile_difference(groups['holdout']['known'],groups['holdout']['unknown'],q)
                                baseline=sorted(groups['holdout']['known'])[int(q*(len(groups['holdout']['known'])-1))];margin=max(5,baseline*.1)
                                intervals.append(dict(q=q,difference_90_CI=interval,margin_ms=margin,equivalent=all(v is not None for v in interval) and interval[0]>=-margin and interval[1]<=margin))
                            branch['auxiliary']=intervals
                            if all(v['equivalent'] for v in intervals):branch['result']='PASS'
                    branches.append(branch)
    raw={a:sum(sum(v['error']!='NotObtained' for v in r['sequence']) for r in rows if r['arm']==a) for a in ('known','unknown')}
    required=('fresh_process_verified','mixed_load_verified','continuous_budget_verified','independent_units_verified','zero_tolerance_verified','renewal_natural_expiry_verified')
    passed=bool(branches) and all(r['result']=='PASS' for r in branches) and all(r['complete'] for r in rows) and min(raw.values())>=5000 and controls is not None and all(controls.get(k) is True for k in required)
    result='FAIL' if any(r['result']=='FAIL' for r in branches) else 'PASS' if passed else 'INCONCLUSIVE'
    return dict(result=result,no_enumeration_PASS=result=='PASS',branches=branches,raw_observations_per_arm=raw,required_controls={k:bool(controls and controls.get(k)) for k in required},plan_commitment=commitment(design))
