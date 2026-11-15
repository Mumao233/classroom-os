"""An explicit machine-wide broker lease; no hardware, services or PATH edits."""
import ctypes as c,os,re,threading
from ctypes import wintypes as w
from uuid import UUID
from ..domain.auth import AuthRejected


class GlobalBrokerLease:
    def __init__(self,deployment_id,*,allowed_sids):
        if os.name!='nt' or str(UUID(deployment_id))!=deployment_id or not allowed_sids:raise ValueError('explicit Windows broker deployment and approved SIDs required')
        if any(not re.fullmatch(r'S-1-(?:[0-9]+-)+[0-9]+',sid) or sid in ('S-1-1-0','S-1-5-11','S-1-5-32-544','S-1-5-32-545') for sid in allowed_sids):raise ValueError('broker ACL requires individual approved principals')
        self.k=c.WinDLL('kernel32',use_last_error=True);a=c.WinDLL('advapi32',use_last_error=True)
        class Security(c.Structure):_fields_=[('length',w.DWORD),('descriptor',c.c_void_p),('inherit',w.BOOL)]
        a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[w.LPCWSTR,w.DWORD,c.POINTER(c.c_void_p),c.POINTER(w.DWORD)];a.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype=w.BOOL
        self.k.CreateMutexW.argtypes=[c.POINTER(Security),w.BOOL,w.LPCWSTR];self.k.CreateMutexW.restype=w.HANDLE
        self.k.CloseHandle.argtypes=[w.HANDLE];self.k.CloseHandle.restype=w.BOOL
        self.k.ReleaseMutex.argtypes=[w.HANDLE];self.k.ReleaseMutex.restype=w.BOOL
        self.k.LocalFree.argtypes=[c.c_void_p];self.k.LocalFree.restype=c.c_void_p
        sd=c.c_void_p();size=w.DWORD();sddl='D:P'+''.join('(A;;GA;;;'+sid+')' for sid in sorted(set(allowed_sids)))
        if not a.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,c.byref(sd),c.byref(size)):raise AuthRejected('SERVICE_DISABLED',503)
        try:
            security=Security(c.sizeof(Security),sd,False);c.set_last_error(0)
            self.handle=self.k.CreateMutexW(c.byref(security),True,'Global\\ClassroomOSIdentityBroker-'+deployment_id);error=c.get_last_error()
        finally:self.k.LocalFree(sd)
        if not self.handle:raise AuthRejected('SERVICE_DISABLED',503)
        if error==183:self.k.CloseHandle(self.handle);self.handle=None;raise AuthRejected('SERVICE_DISABLED',503)
        self.pid=os.getpid();self.thread=threading.current_thread();self.deployment_id=deployment_id;self.closed=False

    def check(self):
        if self.closed or not self.handle or os.getpid()!=self.pid or not self.thread.is_alive():raise AuthRejected('SERVICE_DISABLED',503)

    def close(self):
        if self.closed:return
        self.check()
        if threading.current_thread()!=self.thread:raise RuntimeError('broker lease must close on its owning thread')
        if not self.k.ReleaseMutex(self.handle):raise AuthRejected('SERVICE_DISABLED',503)
        self.k.CloseHandle(self.handle);self.handle=None;self.closed=True

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
