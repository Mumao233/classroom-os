"""Bounded local byte pipes with explicit kernel ACLs and server identity pins.

There is no pickle, remote pipe, secret argv or implicitly trusted pipe name.
The deployment supplies both individual principal SIDs and the trusted server
process ID/SID out of band. Handles are non-inheritable and never logged.
"""
import ctypes as c
from ctypes import wintypes as w
import os,re,struct,threading,time

from ..domain.auth import AuthRejected

MAX_FRAME=65536


class Security(c.Structure):
    _fields_=[('length',w.DWORD),('descriptor',c.c_void_p),('inherit',w.BOOL)]


def api():
    if os.name!='nt':raise ValueError('Windows local pipe adapter required')
    k=c.WinDLL('kernel32',use_last_error=True);a=c.WinDLL('advapi32',use_last_error=True)
    for dll,name,args,result in [
        (k,'CreateNamedPipeW',[w.LPCWSTR,w.DWORD,w.DWORD,w.DWORD,w.DWORD,w.DWORD,w.DWORD,c.POINTER(Security)],w.HANDLE),
        (k,'ConnectNamedPipe',[w.HANDLE,c.c_void_p],w.BOOL),
        (k,'CreateFileW',[w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE],w.HANDLE),
        (k,'SetNamedPipeHandleState',[w.HANDLE,c.POINTER(w.DWORD),c.c_void_p,c.c_void_p],w.BOOL),
        (k,'ReadFile',[w.HANDLE,c.c_void_p,w.DWORD,c.POINTER(w.DWORD),c.c_void_p],w.BOOL),
        (k,'WriteFile',[w.HANDLE,c.c_void_p,w.DWORD,c.POINTER(w.DWORD),c.c_void_p],w.BOOL),
        (k,'GetNamedPipeServerProcessId',[w.HANDLE,c.POINTER(w.ULONG)],w.BOOL),
        (k,'GetCurrentProcess',[],w.HANDLE),(k,'OpenProcess',[w.DWORD,w.BOOL,w.DWORD],w.HANDLE),
        (k,'WaitForSingleObject',[w.HANDLE,w.DWORD],w.DWORD),
        (k,'CloseHandle',[w.HANDLE],w.BOOL),(k,'LocalFree',[c.c_void_p],c.c_void_p),
        (a,'OpenProcessToken',[w.HANDLE,w.DWORD,c.POINTER(w.HANDLE)],w.BOOL),
        (a,'GetTokenInformation',[w.HANDLE,w.DWORD,c.c_void_p,w.DWORD,c.POINTER(w.DWORD)],w.BOOL),
        (a,'ConvertSidToStringSidW',[c.c_void_p,c.POINTER(w.LPWSTR)],w.BOOL),
        (a,'ConvertStringSecurityDescriptorToSecurityDescriptorW',[w.LPCWSTR,w.DWORD,c.POINTER(c.c_void_p),c.POINTER(w.DWORD)],w.BOOL),
    ]:
        f=getattr(dll,name);f.argtypes=args;f.restype=result
    return k,a


def process_sid(process=None):
    k,a=api();token=w.HANDLE();text=w.LPWSTR()
    try:
        if not a.OpenProcessToken(process or k.GetCurrentProcess(),8,c.byref(token)):raise AuthRejected('FORBIDDEN',403)
        size=w.DWORD();a.GetTokenInformation(token,1,None,0,c.byref(size))
        if not 16<=size.value<=65536:raise AuthRejected('FORBIDDEN',403)
        raw=c.create_string_buffer(size.value)
        if not a.GetTokenInformation(token,1,raw,size,c.byref(size)):raise AuthRejected('FORBIDDEN',403)
        if not a.ConvertSidToStringSidW(c.cast(raw,c.POINTER(c.c_void_p))[0],c.byref(text)):raise AuthRejected('FORBIDDEN',403)
        return text.value
    finally:
        if text:k.LocalFree(text)
        if token:k.CloseHandle(token)


def individual_sid(value):
    if not re.fullmatch(r'S-1-(?:[0-9]+-)+[0-9]+',value) or value in ('S-1-1-0','S-1-5-11','S-1-5-32-544','S-1-5-32-545'):
        raise ValueError('explicit individual SID required')
    return value


class PipeChannel:
    def __init__(self,handle,*,server_process=None):
        self.k,_=api();self.handle=handle;self.server_process=server_process;self.lock=threading.RLock();self.closed=False

    def _check(self):
        if self.closed or (self.server_process and self.k.WaitForSingleObject(self.server_process,0)!=258):raise AuthRejected('FORBIDDEN',403)

    def _read(self,count,deadline):
        result=bytearray()
        while len(result)<count:
            self._check()
            if time.monotonic()>=deadline:raise AuthRejected('DEADLINE_EXCEEDED',503)
            raw=c.create_string_buffer(count-len(result));done=w.DWORD()
            if self.k.ReadFile(self.handle,raw,len(raw),c.byref(done),None):
                if done.value:result.extend(raw.raw[:done.value]);continue
            elif c.get_last_error() not in (232,536):raise AuthRejected('FORBIDDEN',403)
            time.sleep(.005)
        return bytes(result)

    def receive(self,timeout=5):
        deadline=time.monotonic()+min(120,max(.01,timeout))
        size=struct.unpack('!I',self._read(4,deadline))[0]
        if not 1<=size<=MAX_FRAME:raise AuthRejected('FORBIDDEN',403)
        return self._read(size,deadline)

    def send(self,raw,timeout=5):
        if not isinstance(raw,bytes) or not 1<=len(raw)<=MAX_FRAME:raise ValueError('bounded byte frame required')
        data=struct.pack('!I',len(raw))+raw;offset=0;deadline=time.monotonic()+min(5,max(.01,timeout))
        while offset<len(data):
            self._check()
            if time.monotonic()>=deadline:raise AuthRejected('DEADLINE_EXCEEDED',503)
            done=w.DWORD();chunk=data[offset:]
            if self.k.WriteFile(self.handle,chunk,len(chunk),c.byref(done),None):offset+=done.value
            elif c.get_last_error() not in (232,536):raise AuthRejected('FORBIDDEN',403)
            if not done.value:time.sleep(.005)

    def close(self):
        with self.lock:
            if self.closed:return
            self.closed=True;self.k.CloseHandle(self.handle)
            if self.server_process:self.k.CloseHandle(self.server_process)

    def __enter__(self):return self
    def __exit__(self,*_):self.close()


class PipeListener:
    def __init__(self,name,*,allowed_sids):
        if not re.fullmatch(r'ClassroomOSIdentity-[a-zA-Z0-9-]{1,100}',name) or not allowed_sids:raise ValueError('explicit private pipe identity required')
        self.name='\\\\.\\pipe\\'+name;self.k,self.a=api();self.allowed_sids=tuple(individual_sid(v) for v in allowed_sids)
        self.closed=False;self.handle=self._create(first=True)

    def _create(self,*,first=False):
        sd=c.c_void_p();size=w.DWORD();sddl='D:P'+''.join('(A;;GA;;;'+sid+')' for sid in sorted(set(self.allowed_sids)))
        if not self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,c.byref(sd),c.byref(size)):raise AuthRejected('SERVICE_DISABLED',503)
        try:
            security=Security(c.sizeof(Security),sd,False)
            # Duplex, first-instance squatting protection, byte mode, NOWAIT,
            # remote clients rejected by the kernel, at most 18 local handles
            # (16 workers plus accept/replacement during saturation).
            handle=self.k.CreateNamedPipeW(self.name,3|(0x80000 if first else 0),1|8,18,65536,65536,0,c.byref(security))
            if handle==c.c_void_p(-1).value:raise AuthRejected('SERVICE_DISABLED',503)
            return handle
        finally:self.k.LocalFree(sd)

    def accept(self,timeout=1):
        deadline=time.monotonic()+timeout
        while not self.closed and time.monotonic()<deadline:
            connected=self.k.ConnectNamedPipe(self.handle,None);error=c.get_last_error()
            if connected or error==535:
                previous=self.handle
                # Keep a server handle alive throughout replacement to prevent
                # another process claiming the same pipe between clients.
                self.handle=self._create()
                return PipeChannel(previous)
            if error not in (536,232):raise AuthRejected('SERVICE_DISABLED',503)
            time.sleep(.005)
        return None

    def close(self):
        if not self.closed:self.closed=True;self.k.CloseHandle(self.handle)

    def __enter__(self):return self
    def __exit__(self,*_):self.close()


def connect_pipe(name,*,expected_server_pid,expected_server_sid,timeout=5):
    if not re.fullmatch(r'ClassroomOSIdentity-[a-zA-Z0-9-]{1,100}',name):raise ValueError('invalid pipe name')
    individual_sid(expected_server_sid);k,_=api();deadline=time.monotonic()+min(5,timeout)
    while time.monotonic()<deadline:
        # SECURITY_IDENTIFICATION allows SID validation but no delegation.
        handle=k.CreateFileW('\\\\.\\pipe\\'+name,0xC0000000,0,None,3,0x100000|0x10000,None)
        if handle!=c.c_void_p(-1).value:break
        if c.get_last_error() not in (2,231):raise AuthRejected('FORBIDDEN',403)
        time.sleep(.005)
    else:raise AuthRejected('SERVICE_DISABLED',503)
    process=None
    try:
        pid=w.ULONG()
        if not k.GetNamedPipeServerProcessId(handle,c.byref(pid)) or pid.value!=expected_server_pid:raise AuthRejected('FORBIDDEN',403)
        process=k.OpenProcess(0x1000|0x100000,False,pid.value)
        if not process or process_sid(process)!=expected_server_sid or k.WaitForSingleObject(process,0)!=258:raise AuthRejected('FORBIDDEN',403)
        mode=w.DWORD(1)
        if not k.SetNamedPipeHandleState(handle,c.byref(mode),None,None):raise AuthRejected('FORBIDDEN',403)
        return PipeChannel(handle,server_process=process)
    except BaseException:
        k.CloseHandle(handle)
        if process:k.CloseHandle(process)
        raise
