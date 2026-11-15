"""Absolute ingress deadlines. Timed-out work retains its slot until it exits."""
from contextlib import contextmanager
from contextvars import ContextVar,copy_context
from concurrent.futures import ThreadPoolExecutor,TimeoutError as FutureTimeout
import threading,time
import common as b
CURRENT=ContextVar('s3_absolute_ingress_deadline',default=None)
OPERATION=ContextVar('s3_absolute_operation_deadline',default=None)
GRANTED=ContextVar('s3_admission_granted_event',default=None)
class Expired(b.Stop):pass
def remaining(deadline=None):
    deadline=CURRENT.get() if deadline is None else deadline
    return None if deadline is None else max(0.0,deadline-time.monotonic())
def live(deadline=None):
    value=remaining(deadline)
    if value is not None and value<=0:raise Expired('ingress_deadline')
    return value
def granted():
    live();event=GRANTED.get();b.require(event is not None,'admission_grant_without_ingress')
    # Admission expires after one second. Already admitted computation and
    # reply retain the SAME entry-based five-second overall operation budget.
    CURRENT.set(OPERATION.get());event.set()
def reclaim_unstarted(control,*,expensive_calls):
    """Resolve a possibly committed reservation whose answer was not received.

    The sole task owner has not called the expensive operation. Poll does not
    create a new job. No caller may use this path for a running computation.
    """
    b.require(expensive_calls==0,'cannot_reclaim_executing_hash')
    token=CURRENT.set(time.monotonic()+5)
    try:
        while True:
            live()
            try:
                state=control('poll')
                state=state['state'] if isinstance(state,dict) else state
                if state=='active':control('finish')
                elif state=='queued':control('cancel')
                return
            except BaseException as ex:
                # A lock-timeout transaction was rolled back by FixedPool.
                # Re-resolve only this existing owned jid; poll never creates
                # a reservation or grants permission to execute a hash. The
                # single cleanup deadline above is never renewed by a retry.
                if getattr(ex,'sqlstate',None)!='55P03':raise
                time.sleep(min(.05,live()))
    finally:CURRENT.reset(token)
@contextmanager
def locked(lock):
    wait=live()
    if not (lock.acquire() if wait is None else lock.acquire(timeout=wait)):raise Expired('ingress_lock_deadline')
    try:live();yield
    finally:lock.release()
class SharedSlots:
    """One private kernel ingress counter shared by both worker processes."""
    def __init__(self,name=None):
        import os,ctypes as c,secrets
        from ctypes import wintypes as w
        import windows
        b.require(os.name=='nt' and b.HOME.exists(),'guest_shared_slots_only')
        self.k,a=windows.api()
        for n,args,ret in (('CreateSemaphoreW',[c.c_void_p,w.LONG,w.LONG,w.LPCWSTR],w.HANDLE),('OpenSemaphoreW',[w.DWORD,w.BOOL,w.LPCWSTR],w.HANDLE),('WaitForSingleObject',[w.HANDLE,w.DWORD],w.DWORD),('ReleaseSemaphore',[w.HANDLE,w.LONG,c.c_void_p],w.BOOL)):
            f=getattr(self.k,n);f.argtypes=args;f.restype=ret
        self.name=name or ('Local\\PR03b-S3-ingress-'+secrets.token_hex(16))
        b.require(self.name.startswith('Local\\PR03b-S3-ingress-') and len(self.name)==len('Local\\PR03b-S3-ingress-')+32,'private_ingress_name')
        if name:self.handle=self.k.OpenSemaphoreW(0x100002,False,name)
        else:
            sd=c.c_void_p();size=w.DWORD()
            b.require(a.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;0x00100002;;;'+windows.sid_string()+')',1,c.byref(sd),c.byref(size)),'ingress_semaphore_DACL')
            sa=windows.Security(c.sizeof(windows.Security),sd,False)
            try:self.handle=self.k.CreateSemaphoreW(c.byref(sa),10,10,self.name)
            finally:self.k.LocalFree(sd)
        b.require(bool(self.handle),'ingress_semaphore_open')
    def acquire(self):return self.k.WaitForSingleObject(self.handle,0)==0
    def release(self):b.require(self.k.ReleaseSemaphore(self.handle,1,None),'ingress_semaphore_release')
_shared=None
_shared_lock=threading.Lock()
def shared():
    global _shared
    with _shared_lock:
        if _shared is None:_shared=SharedSlots()
        return _shared
class Bounded:
    def __init__(self,capacity=10,shared_slots=None):
        b.require(capacity==10,'fixed_ingress_capacity')
        self.slots=threading.BoundedSemaphore(capacity)
        self.shared_slots=shared_slots
        self.pool=ThreadPoolExecutor(max_workers=capacity,thread_name_prefix='fixed-admission')
        self.lock=threading.Lock();self.outstanding=0;self.peak=0;self.closed=False
    def call(self,body,*,deadline=None):
        entered=time.monotonic();deadline=entered+1 if deadline is None else min(deadline,entered+1)
        operation_deadline=min(entered+5,deadline+4);admitted=threading.Event()
        if self.closed or not self.slots.acquire(False):raise Expired('ingress_full')
        if self.shared_slots is not None and not self.shared_slots.acquire():
            self.slots.release();raise Expired('ingress_full')
        with self.lock:self.outstanding+=1;self.peak=max(self.peak,self.outstanding)
        context=copy_context()
        def work():
            token=CURRENT.set(deadline)
            op=OPERATION.set(operation_deadline);grant=GRANTED.set(admitted)
            try:live();return body()
            finally:
                CURRENT.reset(token)
                OPERATION.reset(op);GRANTED.reset(grant)
                with self.lock:self.outstanding-=1
                self.slots.release()
                if self.shared_slots is not None:self.shared_slots.release()
        try:future=self.pool.submit(context.run,work)
        except BaseException:
            with self.lock:self.outstanding-=1
            self.slots.release()
            if self.shared_slots is not None:self.shared_slots.release()
            raise
        try:return future.result(timeout=max(0,deadline-time.monotonic()))
        except FutureTimeout:
            if future.done():return future.result()
            if admitted.is_set():
                try:return future.result(timeout=max(0,operation_deadline-time.monotonic()))
                except FutureTimeout:
                    if future.done():return future.result()
                    raise Expired('operation_deadline') from None
            raise Expired('ingress_wait_deadline') from None
    def close(self):
        self.closed=True;self.pool.shutdown(wait=True,cancel_futures=False)
        b.require(self.outstanding==0,'ingress_work_not_finished')
