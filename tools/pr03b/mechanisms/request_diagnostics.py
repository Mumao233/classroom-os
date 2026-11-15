"""Bounded, secret-free request/stage facts for the isolated S3 prototype."""
from contextlib import contextmanager
from contextvars import ContextVar
from collections import Counter,deque
from pathlib import Path
import re,threading,time,traceback,uuid

CURRENT=ContextVar('s3_request_diagnostic',default=None)
ROUTES={'/lab/page','/lab/preauth','/lab/login','/lab/mfa','/lab/business','/lab/me','/lab/ws-ticket','/lab/reauth','/lab/codes'}
SAFE_CODES={'ingress_full','ingress_deadline','ingress_lock_deadline','ingress_wait_deadline','operation_deadline','pool_wait_deadline','pool_close_deadline','global_hash_capacity','source_pin_reader_deadline','guard_io_reader_deadline'}

def error_facts(error):
    state=getattr(error,'sqlstate',None)
    if type(state) is not str or re.fullmatch('[A-Z0-9]{5}',state) is None:state=None
    args=getattr(error,'args',())
    code=args[0] if args and type(args[0]) is str and args[0] in SAFE_CODES else None
    frames=[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(error.__traceback__)][-8:]
    return {'error_type':type(error).__name__,'sqlstate':state,'safe_code':code,'frames':frames}

class Request:
    def __init__(self,method,route,on_failure=None,client_request_id=None):
        self.lock=threading.RLock();self.start=time.monotonic();self.id=uuid.uuid4().hex
        self.method=method if method in ('GET','POST') else 'other';self.route=route if route in ROUTES else 'other'
        self.client_request_id=client_request_id if type(client_request_id) is str and re.fullmatch('[0-9a-f]{32}',client_request_id) else None
        self.events=deque(maxlen=64);self.counts=Counter();self.first_exception=None
        self.response_status_attempted=None;self.response_write_returned=False;self.handler_finished_ms=None
        self.on_failure=on_failure
    def event(self,stage,*,seconds=None,error=None,secondary=False):
        row={'stage':stage,'since_entry_ms':round((time.monotonic()-self.start)*1000,3),'secondary':secondary}
        if seconds is not None:row['duration_ms']=round(seconds*1000,3)
        if error is not None:row.update(error_facts(error))
        first=False
        with self.lock:
            if error is not None and not secondary and self.first_exception is None:self.first_exception=dict(row);first=True
            self.counts[stage]+=1;self.events.append(row)
        if first and self.on_failure is not None:self.on_failure(self)
    def snapshot(self):
        with self.lock:return {'request_id':self.id,'client_request_id':self.client_request_id,'method':self.method,'route':self.route,'response_status_attempted':self.response_status_attempted,'response_write_returned':self.response_write_returned,'handler_finished_ms':self.handler_finished_ms,'first_exception':self.first_exception,'stage_counts':dict(self.counts),'last_events':list(self.events),'event_capacity':64}

class Recorder:
    def __init__(self):
        self.lock=threading.RLock();self.rows=deque(maxlen=128);self.total=0
        self.first_failures=[];self.last_failures=deque(maxlen=32);self.failure_total=0;self.failure_counts=Counter()
    def failure(self,row):
        with self.lock:
            self.failure_total+=1
            value=row.first_exception
            self.failure_counts[(row.method,row.route,value['stage'],value['error_type'],value['sqlstate'],value['safe_code'])]+=1
            if len(self.first_failures)<32:self.first_failures.append(row)
            else:self.last_failures.append(row)
    @contextmanager
    def request(self,method,route,client_request_id=None):
        row=Request(method,route,self.failure,client_request_id)
        with self.lock:self.total+=1;self.rows.append(row)
        token=CURRENT.set(row)
        try:yield row
        finally:
            row.handler_finished_ms=round((time.monotonic()-row.start)*1000,3)
            CURRENT.reset(token)
    def snapshot(self):
        with self.lock:
            rows=list(self.rows);failures=[*self.first_failures,*self.last_failures]
            value={'total_requests':self.total,'retained_requests':len(rows),'discarded_requests':self.total-len(rows),'request_capacity':128,'failure_requests_total':self.failure_total,'failure_requests_retained':len(failures),'failure_requests_discarded':self.failure_total-len(failures),'failure_counts':[{'method':k[0],'route':k[1],'stage':k[2],'error_type':k[3],'sqlstate':k[4],'safe_code':k[5],'count':n} for k,n in self.failure_counts.items()],'request_values_or_exception_messages_recorded':False}
        value.update(requests=[r.snapshot() for r in rows],first_and_last_failure_requests=[r.snapshot() for r in failures])
        return value

def failure(error,stage,*,secondary=False):
    row=CURRENT.get()
    if row is not None:row.event(stage,error=error,secondary=secondary)

@contextmanager
def stage(name,*,secondary=False):
    row=CURRENT.get()
    if row is None:yield;return
    start=time.monotonic()
    try:yield
    except BaseException as error:
        row.event(name,seconds=time.monotonic()-start,error=error,secondary=secondary);raise
    else:row.event(name,seconds=time.monotonic()-start)
