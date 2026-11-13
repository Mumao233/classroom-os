"""Nonsecret order statistics and conservative simultaneous classifier bounds."""
import bisect,math,statistics

def auc(a,z):
    other=sorted(z)
    return sum(bisect.bisect_left(other,v)+.5*(bisect.bisect_right(other,v)-bisect.bisect_left(other,v)) for v in a)/(len(a)*len(z))

def fit(a,z):
    # Fit both threshold directions ONLY on calibration observations.
    x=sorted(a);y=sorted(z);best=None
    for t in sorted(set(x+y)):
        left_a=bisect.bisect_left(x,t)/len(x);left_z=bisect.bisect_left(y,t)/len(y)
        score=(1-left_a+left_z)/2
        for direction,value in ((True,score),(False,1-score)):
            if best is None or value>best[0]:best=(value,t,direction)
    return {'threshold':best[1],'direction':best[2]}

def evaluate(a,z,frozen,family):
    ba=(sum((v>=frozen['threshold'])==frozen['direction'] for v in a)/len(a)+sum((v>=frozen['threshold'])!=frozen['direction'] for v in z)/len(z))/2
    area=auc(a,z);star=max(area,1-area)
    # Independent complete sequence groups are the units. Prefixes/requests
    # do not increase N. Union bounds include both tails and AUC directions.
    term=math.log(8*family/.05)
    e_b=math.sqrt(term*(1/len(a)+1/len(z))/8)
    e_a=math.sqrt(term*(1/len(a)+1/len(z))/2)
    return {'balanced_accuracy':ba,'AUC_star':star,'BA_95_simultaneous':[max(0,ba-e_b),min(1,ba+e_b)],'AUC_star_95_simultaneous':[max(.5,star-e_a),min(1,star+e_a)],'method':'Hoeffding balanced accuracy and McDiarmid two-sample AUC; Bonferroni family, independent trial groups required'}

def quantile_interval(values,q,alpha=.05):
    """Exact binomial order-rank interval under independent observations.

    None denotes an unbounded endpoint, never an observed sample extremum.
    """
    values=sorted(values);n=len(values)
    if not n:return [None,None]
    logq=math.log(q);logz=math.log1p(-q);total=0.;low=None;high=None
    for rank in range(n+1):
        probability=math.exp(math.lgamma(n+1)-math.lgamma(rank+1)-math.lgamma(n-rank+1)+rank*logq+(n-rank)*logz)
        total+=probability
        if low is None and total>=alpha/2:low=rank
        if high is None and total>=1-alpha/2:high=rank;break
    return [values[low-1] if low is not None and low>0 else None,values[high] if high is not None and high<n else None]

def quantile_difference(a,z,q):
    # Each arm 95%, hence their difference at least 90% by union bound.
    ca=quantile_interval(a,q);cz=quantile_interval(z,q)
    return [None if ca[0] is None or cz[1] is None else ca[0]-cz[1],None if ca[1] is None or cz[0] is None else ca[1]-cz[0]]
