"""Collector fault tests use a private clock; they are not PG/timing evidence."""
import asyncio,copy,json,sys,unittest
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parent.parent/'mechanisms')]
import enumeration_renewal as method
from enumeration_trial import collect

class Clock:
    def __init__(self):self.now=0.
    def __call__(self):return self.now
    async def sleep(self,seconds):self.now+=seconds

class Response:
    def __init__(self,status,value):self.status_code=status;self.value=value;self.content=json.dumps(value).encode()
    def json(self):return self.value

class Cookies:
    def __init__(self):self.present=False;self.clears=0
    def clear(self):self.present=False;self.clears+=1

class Client:
    def __init__(self,clock):self.clock=clock;self.cookies=Cookies();self.n=0;self.window=None;self.created=0;self.calls=[];self.hashes=0
    async def get(self,url,**kwargs):
        self.calls.append(('GET',url,self.clock()))
        if not self.cookies.present or self.clock()-self.created>=300:self.created=self.clock();self.cookies.present=True
        return Response(200,dict(csrf_token='c'*43))
    async def post(self,url,**kwargs):
        self.calls.append(('POST',url,self.clock()))
        self.assert_origin=(url.startswith(kwargs['headers']['Origin']+'/'))
        if self.window is None or self.clock()-self.window>=900:self.window=self.clock();self.n=0
        if self.n>=3:return Response(429,dict(error=dict(code='RATE_LIMITED')))
        self.n+=1;self.hashes+=1
        return Response(401,dict(error=dict(code='UNAUTHENTICATED')))

class Checks(unittest.IsolatedAsyncioTestCase):
    async def trial(self,schedule='quota',client_factory=Client):
        clock=Clock();client=client_factory(clock);events=[]
        row=await collect(client,origin='https://127.0.0.1:4455',origins=['https://127.0.0.1:4455','https://127.0.0.1:4456'],
            email='synthetic@example.test',password='synthetic-wrong-only',target='target-a',trial='trial-a',split='calibration',
            arm='known',cell=0,schedule=schedule,design=method.plan(1),clock=clock,sleep=clock.sleep,emit=events.append)
        return row,client,events

    async def test_renewing_public_context_does_not_reset_budget(self):
        row,client,events=await self.trial()
        self.assertTrue(row['complete']);self.assertEqual([s['status'] for s in row['sequence']],[401]*3+[429]*5)
        self.assertEqual(client.cookies.clears,1);self.assertEqual(client.hashes,3)
        self.assertEqual(len(events),10);self.assertEqual(row['context_count'],2);self.assertTrue(client.assert_origin)
        self.assertTrue(method.validate_units([row],method.plan(1)))

    async def test_natural_wait_preserves_four_admitted_prefix_and_all_rejections(self):
        row,client,_=await self.trial('natural_expiry')
        self.assertEqual([s['status'] for s in row['sequence']],[401]*6+[429]*2)
        self.assertEqual(client.cookies.clears,0);self.assertEqual(client.hashes,6)
        self.assertGreaterEqual(row['protocol'][1]['wait_seconds'],900)
        self.assertGreaterEqual(row['protocol'][1]['start_seconds'],902)
        self.assertTrue(method.validate_units([row],method.plan(1)))

    async def test_failed_renewal_keeps_earlier_observations_and_marks_unsent_tail(self):
        class Bad(Client):
            async def get(self,url,**kwargs):
                if len([c for c in self.calls if c[0]=='GET'])==1:
                    self.calls.append(('GET',url,self.clock()));return Response(503,dict(error=dict(code='SERVICE_DISABLED')))
                return await super().get(url,**kwargs)
        row,client,events=await self.trial(client_factory=Bad)
        self.assertFalse(row['complete']);self.assertEqual(client.hashes,3)
        self.assertEqual(len([c for c in client.calls if c[0]=='POST']),3)
        self.assertTrue(all(s['error']=='NotObtained' and s['ms'] is None for s in row['sequence'][3:]))
        self.assertTrue(any(e['kind']=='csrf' and e['status']==503 for e in events))
        report=method.analyze([row],method.plan(1))
        self.assertEqual(report['result'],'INCONCLUSIVE');self.assertEqual(report['raw_observations_per_arm']['known'],3)

    async def test_actual_timeout_is_not_replayed_or_conflated_with_missing(self):
        class Timeout(Client):
            async def post(self,url,**kwargs):self.calls.append(('POST',url,self.clock()));raise TimeoutError()
        row,client,_=await self.trial(client_factory=Timeout)
        self.assertEqual(len([c for c in client.calls if c[0]=='POST']),1)
        self.assertTrue(row['sequence'][0]['timeout']);self.assertEqual(row['sequence'][0]['error'],'TimeoutError')
        self.assertTrue(all(s['error']=='NotObtained' and not s['timeout'] for s in row['sequence'][1:]))

    async def test_malformed_csrf_never_reaches_login(self):
        class Bad(Client):
            async def get(self,url,**kwargs):return Response(200,dict(csrf_token='invalid'))
        row,client,_=await self.trial(client_factory=Bad)
        self.assertFalse(row['complete']);self.assertEqual(client.hashes,0)

    async def test_external_origin_rejected_before_transport(self):
        clock=Clock();client=Client(clock)
        for origin in ('https://example.com','http://127.0.0.1:4455','https://localhost:4455','https://127.0.0.1:5432','https://user@127.0.0.1:4455'):
            with self.assertRaises(ValueError):
                await collect(client,origin=origin,email='synthetic@example.test',password='x',target='a',trial='t',split='calibration',arm='known',cell=0,schedule='quota',design=method.plan(1))
        self.assertEqual(client.calls,[])

    async def test_targets_splits_application_and_budget_changes_rejected(self):
        row,_,_=await self.trial('natural_expiry');design=method.plan(1)
        for key,value in (('budget_resets',1),('target_changes',1),('application_restarts',1),('source_changes',1),('plan_commitment','bad')):
            with self.assertRaises(AssertionError):method.validate_units([{**row,key:value}],design)
        duplicate={**row,'trial':'trial-b','time_block':'block-b','split':'holdout'}
        with self.assertRaises(AssertionError):method.validate_units([row,duplicate],design)
        bad=copy.deepcopy(row);bad['sequence'][4]['target']='target-b'
        with self.assertRaises(AssertionError):method.validate_units([bad],design)

    async def test_empty_or_small_functional_trial_never_approves_statistics(self):
        design=method.plan(1);empty=method.analyze([],design)
        self.assertEqual(empty['result'],'INCONCLUSIVE');self.assertEqual(len(empty['branches']),design['family_size'])
        row,_,_=await self.trial('natural_expiry')
        for sample in row['sequence']:sample['expensive_calls']=1 if sample['status']==401 else 0
        small=method.analyze([row],design)
        self.assertFalse(small['no_enumeration_PASS']);self.assertTrue(any(b['view']=='admitted' and b['k']==4 and b['counts']['calibration']['known']==1 for b in small['branches']))

    async def test_functional_records_cannot_be_relabelled_as_normative_implicitly(self):
        row,_,_=await self.trial()
        with self.assertRaises(AssertionError):method.analyze([{**row,'split':'functional'}],method.plan(1))

    async def test_original_minima_thresholds_and_legacy_plan_are_preserved(self):
        design=method.plan(1);legacy=method.legacy.plan(1)
        self.assertEqual(legacy['schema_version'],2);self.assertEqual(legacy['context'],'fresh real preauth per trial before first observation; no replacement within its eight-request sequence')
        for k in ('min_sequence_groups_per_cell_split_arm','min_raw_observations_per_arm','max_trial_seconds','PASS_rule'):
            self.assertEqual(design[k],legacy[k])
        self.assertEqual(design['account_failure_limit'],3);self.assertEqual(design['account_window_seconds'],900);self.assertEqual(design['preauth_seconds'],300)
        ready=method.readiness(design,None)
        self.assertFalse(ready['execution_ready']);self.assertEqual(ready['minimum_independent_arm_sequences'],864000)
        self.assertEqual(ready['minimum_scheduled_login_requests'],6912000)
        self.assertGreaterEqual(design['family_size'],legacy['family_size'])

if __name__=='__main__':unittest.main(verbosity=2)
