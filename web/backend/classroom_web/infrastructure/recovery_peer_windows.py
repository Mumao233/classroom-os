"""Read a connected local named-pipe peer from kernel tokens, never JSON."""
import ctypes as c,os,threading
from ctypes import wintypes as w
from ..domain.auth import AuthRejected


def _api():
    if os.name!='nt':raise RuntimeError('Windows named-pipe peer adapter required')
    k=c.WinDLL('kernel32',use_last_error=True);a=c.WinDLL('advapi32',use_last_error=True)
    bindings=[(k,'GetCurrentProcess',[],w.HANDLE),(k,'GetCurrentThread',[],w.HANDLE),
      (k,'DuplicateHandle',[w.HANDLE,w.HANDLE,w.HANDLE,c.POINTER(w.HANDLE),w.DWORD,w.BOOL,w.DWORD],w.BOOL),
      (k,'GetNamedPipeClientProcessId',[w.HANDLE,c.POINTER(w.ULONG)],w.BOOL),
      (k,'OpenProcess',[w.DWORD,w.BOOL,w.DWORD],w.HANDLE),(k,'WaitForSingleObject',[w.HANDLE,w.DWORD],w.DWORD),
      (k,'CloseHandle',[w.HANDLE],w.BOOL),(k,'LocalFree',[c.c_void_p],c.c_void_p),
      (a,'ImpersonateNamedPipeClient',[w.HANDLE],w.BOOL),(a,'RevertToSelf',[],w.BOOL),
      (a,'OpenThreadToken',[w.HANDLE,w.DWORD,w.BOOL,c.POINTER(w.HANDLE)],w.BOOL),
      (a,'GetTokenInformation',[w.HANDLE,w.DWORD,c.c_void_p,w.DWORD,c.POINTER(w.DWORD)],w.BOOL),
      (a,'ConvertSidToStringSidW',[c.c_void_p,c.POINTER(w.LPWSTR)],w.BOOL)]
    for dll,name,args,result in bindings:f=getattr(dll,name);f.argtypes=args;f.restype=result
    return k,a


class NativePipePeer:
    def __init__(self,connected_server_pipe_handle:int):
        self.k,self.a=_api();self.lock=threading.RLock();self.closed=False;self.pipe=w.HANDLE();self.process=None
        process=self.k.GetCurrentProcess()
        if not self.k.DuplicateHandle(process,connected_server_pipe_handle,process,c.byref(self.pipe),0,False,2):raise AuthRejected('FORBIDDEN',403)
        try:
            pid=w.ULONG()
            if not self.k.GetNamedPipeClientProcessId(self.pipe,c.byref(pid)):raise AuthRejected('FORBIDDEN',403)
            self.pid=pid.value;self.process=self.k.OpenProcess(0x1000|0x100000,False,self.pid)
            if not self.process:raise AuthRejected('FORBIDDEN',403)
            self.subject()
        except BaseException:self.close();raise

    def subject(self):
        with self.lock:
            pid=w.ULONG();token=w.HANDLE();text=w.LPWSTR()
            if self.closed or not self.k.GetNamedPipeClientProcessId(self.pipe,c.byref(pid)) or pid.value!=self.pid or self.k.WaitForSingleObject(self.process,0)!=258:
                raise AuthRejected('FORBIDDEN',403)
            if not self.a.ImpersonateNamedPipeClient(self.pipe):raise AuthRejected('FORBIDDEN',403)
            try:
                if not self.a.OpenThreadToken(self.k.GetCurrentThread(),8,True,c.byref(token)):raise AuthRejected('FORBIDDEN',403)
                needed=w.DWORD();self.a.GetTokenInformation(token,1,None,0,c.byref(needed))
                if not 16<=needed.value<=65536:raise AuthRejected('FORBIDDEN',403)
                data=c.create_string_buffer(needed.value)
                if not self.a.GetTokenInformation(token,1,data,needed,c.byref(needed)):raise AuthRejected('FORBIDDEN',403)
                sid=c.cast(data,c.POINTER(c.c_void_p))[0]
                if not self.a.ConvertSidToStringSidW(sid,c.byref(text)):raise AuthRejected('FORBIDDEN',403)
                return {'provider':'windows_sid','subject':text.value}
            finally:
                if text:self.k.LocalFree(text)
                if token:self.k.CloseHandle(token)
                if not self.a.RevertToSelf():raise AuthRejected('FORBIDDEN',403)

    def close(self):
        with self.lock:
            if self.closed:return
            self.closed=True
            if self.process:self.k.CloseHandle(self.process)
            if self.pipe:self.k.CloseHandle(self.pipe)

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
