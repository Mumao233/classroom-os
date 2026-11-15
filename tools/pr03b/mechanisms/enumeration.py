"""Mandatory calibrated holdout/aggregation; insufficient samples never PASS."""
import hashlib,json,math,random,secrets,time
import common as b,transport
def auc(a,z):return sum(1 if x>y else .5 if x==y else 0 for x in a for y in z)/(len(a)*len(z))
def experiments(lease,plan,check):
    """The old 20-pair collector is not an executable acceptance path.

    Its single preauth context expired during collection.  R05 requires a
    complete new collector, independent trials and capacity prerequisites;
    neither a fresh context per request nor a smaller diagnostic is a fix.
    Keep the reviewed historical implementation below for source comparison.
    """
    raise b.Stop('R05_full_collector_not_validated_no_legacy_fallback')

def historical_twenty_pair_collector_not_for_execution(lease,plan,check):
    root=b.HOME/'enumeration-TLS-private';root.mkdir();lab=transport.Lab(lease,root)
    # Pre-register before requests. No quota reset, alternate source IP, cheap
    # denial mixing, target replacement mid-sequence, or synthetic timings.
    seed=secrets.randbits(64);rng=random.Random(seed)
    design={'seed':seed,'ks':[1,2,4,8],'aggregates':['mean','median','full_status_length_sequence'],'split':'first ten targets calibration; next ten holdout','strata':['admitted','full_response_sequence'],'source':'one literal loopback','CI':'familywise Hoeffding BA / McDiarmid AUC 95% simultaneous bounds','PASS_upper':.55,'no_mid_sequence_reset':True,'capacity_claim':False}
    b.write(plan.run_root/'ENUMERATION-PREREGISTRATION.json',design)
    rows=[]
    try:
        code,pre,headers=transport.request(lab,'/lab/preauth');b.require(code==200,'enum_preauth');cookie=headers['Set-Cookie'].split(';',1)[0]
        for index in range(20):
            # Activated invitations are the known PG credentials; emails after
            # activation are unchanged, unlike the owner1 email-change fixture.
            arms=[('known','invite'+str(index)+'@example.invalid'),('unknown','absent'+str(index)+'@example.invalid')];rng.shuffle(arms)
            for arm,target in arms:
                sequence=[]
                for repeat in range(8):
                    started=time.perf_counter();status,body,_=transport.request(lab,'/lab/login',{'username':target,'password':'synthetic-wrong-input','remember_me':False},csrf=pre['csrf'],headers={'Cookie':cookie});ms=(time.perf_counter()-started)*1000
                    sequence.append({'ms':round(ms,4),'status':status,'length':len(json.dumps(body,separators=(',',':')).encode()),'code':body.get('code'),'admitted':status==401})
                rows.append({'target_index':index,'arm':arm,'split':'calibration' if index<10 else 'holdout','sequence':sequence})
        family=4*3*2;out=[]
        import statistics
        for k in design['ks']:
            for aggregate in design['aggregates']:
                def score(row):
                    seq=row['sequence'][:k]
                    if aggregate=='full_status_length_sequence':return tuple((x['status'],x['length'],x['code']) for x in seq)
                    times=[x['ms'] for x in seq];return statistics.mean(times) if aggregate=='mean' else statistics.median(times)
                for stratum in ('admitted','full_response_sequence'):
                    selected=[r for r in rows if stratum!='admitted' or all(v['admitted'] for v in r['sequence'][:k])]
                    calibration={arm:[score(r) for r in selected if r['arm']==arm and r['split']=='calibration'] for arm in ('known','unknown')}
                    test={arm:[score(r) for r in selected if r['arm']==arm and r['split']=='holdout'] for arm in ('known','unknown')}
                    counts={split:{arm:len(values) for arm,values in group.items()} for split,group in (('calibration',calibration),('holdout',test))}
                    branch={'k':k,'aggregate':aggregate,'stratum':stratum,'counts':counts,'result':'INCONCLUSIVE','simultaneous_bounds_BA':None,'simultaneous_bounds_AUC_star':None,'quantile_equivalence_90CI':None}
                    if min(n for group in counts.values() for n in group.values())==0:out.append(branch);continue
                    candidates=sorted(set(calibration['known']+calibration['unknown']))
                    def ba(a,z,threshold,direction):return (sum((x>=threshold)==direction for x in a)/len(a)+sum((x>=threshold)!=direction for x in z)/len(z))/2
                    threshold,direction=max(((v,d) for v in candidates for d in (False,True)),key=lambda v:ba(calibration['known'],calibration['unknown'],*v))
                    acc=ba(test['known'],test['unknown'],threshold,direction);a=auc(test['known'],test['unknown']);astar=max(a,1-a)
                    # Independent target pairs, never Na*Nb independent samples.
                    epsilon=math.sqrt(math.log(4*family/.05)/(2*min(counts['holdout'].values())))
                    epsilon_auc=math.sqrt(math.log(4*family/.05)*.5*(1/counts['holdout']['known']+1/counts['holdout']['unknown']))
                    branch.update(calibration_threshold=threshold,direction=direction,balanced_accuracy=acc,AUC_star=astar,simultaneous_bounds_BA=[max(0,acc-epsilon),min(1,acc+epsilon)],simultaneous_bounds_AUC_star=[max(.5,astar-epsilon_auc),min(1,astar+epsilon_auc)])
                    if max(branch['simultaneous_bounds_BA'][0],branch['simultaneous_bounds_AUC_star'][0])>.55:branch['result']='FAIL'
                    out.append(branch)
        explicit_leak=any(r['result']=='FAIL' for r in out)
        result='FAIL' if explicit_leak else 'INCONCLUSIVE'
        report={'result':result,'design':design,'branches':out,'rows':rows,'normal_response_variants':sorted({(v['status'],v['length'],v['code']) for r in rows for v in r['sequence']}),'limitations':['Admitted-only branch lacks enough observations after real account/IP failure limits.','No p99/equivalence or no-enumeration acceptance is inferred from N=10 per holdout arm.','Full quota response sequences are evaluated separately from admitted hash timings.'],'PASS':False}
        b.write(plan.run_root/'ENUMERATION.json',report)
        check('A04_all_required_comparisons_evaluated_or_insufficient',len(out)==24 and all(x['result'] in ('FAIL','INCONCLUSIVE') for x in out))
        check('A04_insufficient_evidence_never_PASS',report['PASS'] is False)
        return {k:v for k,v in report.items() if k!='rows'}
    finally:lab.close()
