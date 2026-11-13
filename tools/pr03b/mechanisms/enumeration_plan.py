"""R05 immutable sampling units and independent held-out analysis.

No network/SQL is performed here. Sample deficits cannot become a PASS.
"""
import hashlib,json,statistics
import evidence_stats as es
KS=(1,2,4,8)
AGGREGATES=('mean','median','status_length_sequence')

def readiness(design, capacity, *, actual_account_failure_limit):
    """Evidence eligibility only: never launches a trial or changes a limit."""
    import math
    reasons=[]
    if capacity is None or capacity.get('result')!='PASS':
        reasons.append('R02_R04_resource_and_capacity_prerequisite_not_PASS')
    if actual_account_failure_limit<4:
        reasons.append('admitted_k4_conflicts_with_actual_account_failure_budget')
    reasons.append('full_independent_trial_collector_not_implemented_or_guest_validated')
    minimum=design['min_sequence_groups_per_cell_split_arm']
    sequences=len(design['cells'])*len(design['splits'])*2*minimum
    return {'result':'INCONCLUSIVE','execution_ready':False,'reasons':reasons,
        'minimum_independent_arm_sequences':sequences,
        'minimum_scheduled_raw_requests':sequences*design['sequence_requests'],
        'actual_account_failure_limit':actual_account_failure_limit,
        'required_admitted_ks':[1,2,4],
        'lower_bound_not_a_sufficient_sample_size':True,
        'AUC_bound_half_width_at_N1000_per_arm':math.sqrt(math.log(8*design['family_size']/.05)/1000),
        'budget_or_threshold_modified':False,'database_connections':0}

def plan(seed):
    cells=[]
    for role,lengths in (('student',(6,12,64,1024,'unicode')),('admin',(12,64,1024,'unicode'))):
        for length in lengths:
            for state in ('active','pending','disabled'):
                for thermal in ('fresh_process','warm'):
                    for load in ('idle','normal_mixed'):
                        cells.append({'id':len(cells),'role':role,'length':length,'state':state,'thermal':thermal,'load':load})
    return {'schema_version':2,'seed':seed,'cells':cells,'ks':KS,'aggregates':AGGREGATES,'splits':['calibration','holdout'],'min_sequence_groups_per_cell_split_arm':1000,'min_raw_observations_per_arm':5000,'sequence_requests':8,'views':['full_sequence','admitted_k1_k2_k4'],'family_size':len(cells)*(4+3)*len(AGGREGATES),'max_trial_seconds':1800,'app_instances':'new disposable application per complete arm/trial; at most 30 active synthetic users per instance','split_unit':'complete trial, target identity AND time block; no overlap between calibration and holdout','context':'fresh real preauth per trial before first observation; no replacement within its eight-request sequence','limits':'all existing account, source, global, backoff and admission controls remain enabled; no budget reset within a trial','required_continuous_control':'separate unrecreated instance demonstrates cross-process/entry exhaustion, backoff and recovery','features':['public end-to-end ms','status','serialized response bytes','generic error code','rejection position','timeouts'], 'stop_rules':['guard/isolation/identity/resource failure: STOP; preserve partial raw rows','at fixed 5s per call or 1800s trial budget, retain timeout/stop marker and all later scheduled observations as not obtained','resource/capacity prerequisite failure: INCONCLUSIVE, no old short sampler fallback'],'PASS_rule':'all mandatory BA and AUC* simultaneous upper bounds <=.55 AND auxiliary quantile equivalence AND zero-tolerance public errors pass; missing branch/independent groups/CI is INCONCLUSIVE; proven distinction takes FAIL precedence','sample_reduction_allowed':False}

def validate_units(rows,design):
    seen=set();targets={};blocks={};trials={}
    for r in rows:
        unit=(r['trial'],r['arm']);assert unit not in seen;seen.add(unit)
        assert r['split'] in design['splits'] and r['arm'] in ('known','unknown')
        assert len(r['sequence'])<=8 and r['cell'] in range(len(design['cells']))
        if r['trial'] in trials:assert trials[r['trial']]==r['split'],'split_trial'
        trials[r['trial']]=r['split']
        identity=(r['split'],r['trial'],r['arm'])
        if r['target'] in targets:assert targets[r['target']]==identity,'reused_target_is_not_independent'
        targets[r['target']]=identity
        block=(r['split'],r['trial'])
        if r['time_block'] in blocks:assert blocks[r['time_block']]==block,'time_block_is_not_independent'
        blocks[r['time_block']]=block
        assert r['context_count']==1 and r['budget_resets']==0
    return True

def analyze(rows,design,controls=None):
    validate_units(rows,design);branches=[];family=design['family_size'];raw={a:sum(len(r['sequence']) for r in rows if r['arm']==a) for a in ('known','unknown')}
    for cell in design['cells']:
        for view,ks in (('full_sequence',KS),('admitted',(1,2,4))):
            for k in ks:
                selected=[r for r in rows if r['cell']==cell['id'] and len(r['sequence'])>=k and (view!='admitted' or all(v['status']==401 and v.get('expensive_calls')==1 for v in r['sequence'][:k]))]
                for aggregate in AGGREGATES:
                    def score(row):
                        seq=row['sequence'][:k]
                        if aggregate=='status_length_sequence':return tuple((v['status'] if v['status'] is not None else -1,v['length'],v['code'] or '',v['timeout']) for v in seq)
                        values=[v['ms'] for v in seq]
                        return statistics.mean(values) if aggregate=='mean' else statistics.median(values)
                    groups={split:{arm:[score(r) for r in selected if r['split']==split and r['arm']==arm] for arm in ('known','unknown')} for split in design['splits']}
                    counts={split:{arm:len(v) for arm,v in arms.items()} for split,arms in groups.items()}
                    branch={'cell':cell['id'],'view':view,'k':k,'aggregate':aggregate,'counts':counts,'result':'INCONCLUSIVE'}
                    minimum=min(n for arms in counts.values() for n in arms.values())
                    if minimum==0:branches.append(branch);continue
                    frozen=es.fit(groups['calibration']['known'],groups['calibration']['unknown'])
                    commitment=hashlib.sha256(json.dumps(frozen,sort_keys=True).encode()).hexdigest()
                    result=es.evaluate(groups['holdout']['known'],groups['holdout']['unknown'],frozen,family)
                    branch.update(frozen=frozen,calibration_commitment=commitment,**result)
                    if max(result['BA_95_simultaneous'][0],result['AUC_star_95_simultaneous'][0])>.55:branch['result']='FAIL'
                    elif minimum>=1000 and max(result['BA_95_simultaneous'][1],result['AUC_star_95_simultaneous'][1])<=.55:
                        if aggregate=='status_length_sequence':branch['result']='PASS' if set(groups['holdout']['known'])==set(groups['holdout']['unknown']) else 'FAIL'
                        else:
                            pairs=[]
                            for q in (.5,.95):
                                interval=es.quantile_difference(groups['holdout']['known'],groups['holdout']['unknown'],q)
                                baseline=sorted(groups['holdout']['known'])[int(q*(len(groups['holdout']['known'])-1))];margin=max(5,baseline*.1)
                                pairs.append({'q':q,'difference_90_CI':interval,'margin_ms':margin,'equivalent':all(v is not None for v in interval) and interval[0]>=-margin and interval[1]<=margin})
                            branch['auxiliary']=pairs
                            if all(v['equivalent'] for v in pairs):branch['result']='PASS'
                    branches.append(branch)
    result='FAIL' if any(r['result']=='FAIL' for r in branches) else 'PASS' if branches and all(r['result']=='PASS' for r in branches) and min(raw.values())>=5000 else 'INCONCLUSIVE'
    required=('fresh_process_verified','mixed_load_verified','continuous_budget_verified','independent_units_verified','zero_tolerance_verified')
    supported=controls is not None and all(controls.get(k) is True for k in required)
    if result=='PASS' and not supported:result='INCONCLUSIVE'
    return {'result':result,'raw_observations_per_arm':raw,'branches':branches,'no_enumeration_PASS':result=='PASS','required_controls':{k:bool(controls and controls.get(k)) for k in required},'method_limit':'bounds require independent complete trial groups; the caller must establish fresh-process, mixed-load, continuous-budget and zero-tolerance controls before overall acceptance'}
