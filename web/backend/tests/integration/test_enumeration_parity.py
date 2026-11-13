"""R05 at Alpha scope: known and unknown accounts are indistinguishable at login.

This is the small-sample check agreed in docs/web-alpha/pr03b/SCOPE-DECISION-20261112.md.
It verifies the design properties (same response, same budget behaviour, same Argon2
work) on real PostgreSQL and records coarse timing. Nine attempts per arm cannot
support a statistical claim; the original preregistered campaign was not run.
"""
import statistics,time
from uuid import uuid4

import sqlalchemy as sa

from test_authentication import auth,post,BASE

ACCOUNTS=3;ATTEMPTS=3  # 18 failures in total stays inside the 20/60s per-source budget
WRONG='wrong-synthetic-password'
VOLATILE={'request_id','trace_id','timestamp'}


def _shape(response):
    body=response.json();error=body.get('error',{}) if isinstance(body,dict) else {}
    return dict(status=response.status_code,body_keys=sorted(body) if isinstance(body,dict) else None,
                error={k:v for k,v in error.items() if k not in VOLATILE} if isinstance(error,dict) else error,
                headers=sorted(k.lower() for k in response.headers if k.lower() not in ('date','content-length')),
                cookies=sorted(response.cookies.keys()))


def test_known_and_unknown_accounts_get_the_same_answer_budget_and_hash_work(auth,monkeypatch):
    a=auth;marker=uuid4().hex[:10]
    known=[f'known-{marker}-{i}@example.test' for i in range(ACCOUNTS)]
    unknown=[f'absent-{marker}-{i}@example.test' for i in range(ACCOUNTS)]
    phc=a.crypto.hash('Synthetic-parity-only-20261112')
    with a.pg.admin.begin() as c:
        for email in known:
            c.execute(sa.text("INSERT INTO users(id,normalized_email,password_hash,role,state,email_verified_at) VALUES(:id,:email,:phc,'student','active',clock_timestamp())"),dict(id=uuid4(),email=email,phc=phc))

    hashes=[];verify=a.crypto.verify
    def timed(stored,password):
        start=time.perf_counter()
        try:return verify(stored,password)
        finally:hashes.append((stored is None,time.perf_counter()-start))
    monkeypatch.setattr(a.crypto,'verify',timed)

    def attempt(email):
        a.client.cookies.clear();csrf=a.client.get(BASE+'/auth/csrf').json()['csrf_token']
        before=len(hashes);start=time.perf_counter()
        r=post(a,'/auth/login',dict(email=email,password=WRONG,remember_me=False,device_name='parity'),csrf)
        return _shape(r),time.perf_counter()-start,len(hashes)-before

    seconds={'known':[],'unknown':[]}
    # Alternate arms and swap which goes first so drift does not favour either one.
    for index in range(ATTEMPTS+1):
        for account in range(ACCOUNTS):
            order=(('known',known[account]),('unknown',unknown[account]))
            if (index+account)%2:order=order[::-1]
            seen={}
            for arm,email in order:
                shape,elapsed,hash_calls=attempt(email);seen[arm]=shape
                limited=index==ATTEMPTS
                assert shape['status']==(429 if limited else 401),(arm,index,shape)
                # A rejected-by-budget attempt must not buy an Argon2 computation.
                assert hash_calls==(0 if limited else 1),(arm,index,hash_calls)
                if not limited:seconds[arm].append(elapsed)
            assert seen['known']==seen['unknown'],seen

    known_hash=[d for missing,d in hashes if not missing];unknown_hash=[d for missing,d in hashes if missing]
    assert len(known_hash)==len(unknown_hash)==ACCOUNTS*ATTEMPTS
    # A missing account must run the real Argon2id parameters, not a cheap placeholder.
    assert a.crypto.dummy.startswith('$argon2id$v=19$m=65536,t=3,p=1$')
    assert phc.startswith('$argon2id$v=19$m=65536,t=3,p=1$')

    summary=dict(samples_per_arm=ACCOUNTS*ATTEMPTS,
        known_hash_median_ms=round(statistics.median(known_hash)*1000,2),unknown_hash_median_ms=round(statistics.median(unknown_hash)*1000,2),
        known_request_median_ms=round(statistics.median(seconds['known'])*1000,2),unknown_request_median_ms=round(statistics.median(seconds['unknown'])*1000,2))
    print('R05_PARITY',summary)
    # Coarse guard only: catches a skipped or shortened hash on one arm, tolerates VM noise.
    for left,right in ((known_hash,unknown_hash),(seconds['known'],seconds['unknown'])):
        ratio=statistics.median(left)/statistics.median(right)
        assert 0.5<ratio<2.0,summary
