"""Fault tests for burst evidence accounting, without starting a service."""
import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from admission_client import evaluate

def evidence(api_count=2):
    rows=[];metrics=[];at=0.
    def event(stage,op,pid=0):
        nonlocal at
        at+=.01;metrics.append(dict(stage=stage,op='r04_'+op,pid=pid,time=at,seconds=.01))
    for burst in (16,32):
        for i in range(burst):rows.append(dict(burst=burst,kind='burst',op=f'{burst}-{i}',status=200 if i<10 else 429,error=None,seconds=.01))
        rows.append(dict(burst=burst,kind='recovery',op=f'recovery-{burst}',status=200,error=None,seconds=.1))
        for i in range(2):event('active_enter',f'{burst}-{i}',i%api_count)
        for i in range(2,10):event('wait_enter',f'{burst}-{i}',i%api_count)
        for i in range(10,burst):event('queue_full',f'{burst}-{i}',i%api_count)
        for i in range(2):event('hash_compute',f'{burst}-{i}',i%api_count);event('active_leave',f'{burst}-{i}',i%api_count)
        for i in range(2,10):
            event('wait_leave',f'{burst}-{i}',i%api_count);event('active_enter',f'{burst}-{i}',i%api_count)
            event('hash_compute',f'{burst}-{i}',i%api_count);event('active_leave',f'{burst}-{i}',i%api_count)
    return rows,metrics

class Checks(unittest.TestCase):
    def test_original_shared_bounds_across_process_counts(self):
        for count in (1,2,4):
            rows,metrics=evidence(count);result=evaluate(rows,metrics,count)
            self.assertTrue(result['passed']);self.assertEqual(result['active_peak'],2);self.assertEqual(result['waiting_peak'],8)
            self.assertEqual(result['full_queue_count'],28);self.assertFalse(result['capacity_acceptance']);self.assertFalse(result['tail_approval'])
    def test_hash_on_rejected_request_is_a_failure(self):
        rows,metrics=evidence();metrics.append(dict(stage='hash_compute',op='r04_16-10',pid=0,time=100,seconds=.1))
        self.assertFalse(evaluate(rows,metrics,2)['passed'])
    def test_scope_slot_overflow_cannot_pass(self):
        rows,metrics=evidence();metrics.append(dict(stage='active_enter',op='r04_extra',pid=0,time=0,seconds=0))
        self.assertFalse(evaluate(rows,metrics,2)['passed'])
    def test_full_queue_latency_threshold_stays_half_a_second(self):
        rows,metrics=evidence();next(r for r in rows if r['op']=='16-10')['seconds']=.501
        self.assertFalse(evaluate(rows,metrics,2)['passed'])
    def test_missing_rows_cannot_be_reported_as_complete(self):
        rows,metrics=evidence()
        with self.assertRaises(AssertionError):evaluate(rows[1:],metrics,2)
    def test_http_error_and_failed_recovery_are_retained(self):
        for kind in ('burst','recovery'):
            rows,metrics=evidence();target=next(r for r in rows if r['kind']==kind)
            target['error']='TimeoutError';target['status']=None
            result=evaluate(rows,metrics,2);self.assertFalse(result['passed']);self.assertTrue(result['all_observations_retained'])
    def test_declared_api_count_requires_actual_hash_processes(self):
        rows,metrics=evidence(2);self.assertFalse(evaluate(rows,metrics,4)['passed'])

if __name__=='__main__':unittest.main(verbosity=2)
