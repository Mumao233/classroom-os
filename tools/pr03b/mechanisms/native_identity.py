"""Read-only Win64 identity primitives; every call queries the kernel afresh."""
import ctypes as c,hashlib,json,os,threading,time
from ctypes import wintypes as w
from pathlib import Path
from contextlib import contextmanager
import common as b,admission

class Rejected(RuntimeError):pass

class Library:
    def __init__(self):
        b.require(os.name=='nt' and c.sizeof(c.c_void_p)==8 and c.sizeof(c.c_wchar)==2,'native_identity_win64')
        root=Path(__file__).resolve().parent
        provenance=b.strict((root/'native_identity.build.json').read_bytes())
        sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        b.require(sha(root/'native_identity.dll')==provenance['artifact_sha256'] and sha(root/'native_identity.c')==provenance['source_sha256'],'native_identity_build_changed')
        # The runtime installer has already verified/pinned the entire source
        # bundle, including this DLL. The build JSON alone is not authority.
        self.library=c.WinDLL(str(root/'native_identity.dll'),use_last_error=True)
        tail=[c.POINTER(c.c_ulonglong),w.LPWSTR,w.DWORD,c.POINTER(w.DWORD),c.POINTER(w.DWORD)]
        self.pid_function=self.library.pr03b_observe_pid;self.pid_function.argtypes=[w.DWORD,*tail];self.pid_function.restype=w.BOOL
        self.handle_function=self.library.pr03b_observe_handle;self.handle_function.argtypes=[w.HANDLE,w.DWORD,*tail];self.handle_function.restype=w.BOOL
        self.lock=threading.RLock();self.cv=threading.Condition(self.lock);self.active=0;self.calls=0;self.failures=0;self.closing=False;self.closed=False
    @contextmanager
    def borrowed(self):
        with admission.locked(self.lock):
            b.require(not self.closed and not self.closing,'native_identity_closed');self.active+=1
        try:yield
        finally:
            with self.lock:self.active-=1;self.cv.notify_all()
    def _query(self,pid,handle=None,*,by_handle=False):
        if type(pid) is not int or not 0<pid<=0xffffffff:raise Rejected('invalid_pid')
        with self.borrowed():
            created=c.c_ulonglong();image=c.create_unicode_buffer(32768);length=w.DWORD();error=w.DWORD()
            values=[pid,c.byref(created),image,len(image),c.byref(length),c.byref(error)]
            # WinDLL releases the GIL for the whole native query. The
            # request watchdog and other readers remain independently runnable.
            ok=self.handle_function(handle,*values) if by_handle else self.pid_function(*values)
            with self.lock:self.calls+=1;self.failures+=not bool(ok)
            admission.live()
            if not ok:raise Rejected('kernel_'+str(error.value))
            if not created.value or not 0<length.value<len(image) or len(image.value)!=length.value:raise Rejected('invalid_shape')
            return created.value,image.value
    def pid(self,pid):return self._query(pid)
    def handle(self,handle,pid):return self._query(pid,handle,by_handle=True)
    def statistics(self):
        with self.lock:return {'queries':self.calls,'query_failures':self.failures,'active':self.active,'facts_cached':False,'native_calls_release_GIL':True,'query_only':True}
    def close(self):
        with self.lock:
            if self.closed:return
            self.closing=True;until=time.monotonic()+5
            while self.active:
                remain=until-time.monotonic();b.require(remain>0,'native_identity_queries_not_drained');self.cv.wait(remain)
            kernel=c.WinDLL('kernel32',use_last_error=True);kernel.FreeLibrary.argtypes=[w.HMODULE];kernel.FreeLibrary.restype=w.BOOL
            b.require(kernel.FreeLibrary(self.library._handle),'native_identity_unload_failed');self.closed=True
