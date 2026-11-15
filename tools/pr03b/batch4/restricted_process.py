"""Instance-local CreateProcessAsUser adapter; original Job lifecycle is inherited.

No process creation on import. Never retry with an unrestricted token. The
coordinator remains administrator to observe the already-isolated guest OS.
"""
from __future__ import annotations
import ctypes as c
from ctypes import wintypes as w
from dataclasses import dataclass
import os
import hashlib
import struct
import threading
import native_process as native
from common import require

class SidAttr(c.Structure):
    _fields_ = [('Sid',c.c_void_p),('Attributes',w.DWORD)]

class Groups(c.Structure):
    _fields_ = [('Count',w.DWORD),('Items',SidAttr*1)]

class Luid(c.Structure):
    _fields_ = [('LowPart',w.DWORD),('HighPart',w.LONG)]

class Privilege(c.Structure):
    _fields_ = [('Luid',Luid),('Attributes',w.DWORD)]

class Privileges(c.Structure):
    _fields_ = [('Count',w.DWORD),('Items',Privilege*1)]

@dataclass(frozen=True)
class TokenInfo:
    buffer: object
    kind: int
    required: int
    returned: int

    def receipt(self):
        return {'class':self.kind,'required_length':self.required,'returned_length':self.returned,
                'capacity':c.sizeof(self.buffer),'dword_aligned':c.addressof(self.buffer)%4==0,
                'sizing_error':122,'read_success':True}

def valid_length(kind,size):
    if kind==21:return size==1  # BOOLEAN payload; DWORD alignment/capacity are separate.
    if kind==8:return size==c.sizeof(w.DWORD)
    if kind==2:return Groups.Items.offset<=size<=1024*1024
    if kind==3:return Privileges.Items.offset<=size<=1024*1024
    return False

def filtered_flag(info):
    require(info.kind==21 and info.returned==1 and info.required==1
        and c.sizeof(info.buffer)>=4 and c.addressof(info.buffer)%4==0,'token_restrictions_shape')
    # Bytes 1..3 are not returned payload. Never interpret caller padding as DWORD.
    value=c.c_ubyte.from_buffer(info.buffer).value
    require(value in (0,1),'token_restrictions_value')
    return value==1

def groups_ok(rows):
    # SE_GROUP_ENABLED=4; SE_GROUP_USE_FOR_DENY_ONLY=16.
    return all(not attributes & 4 and bool(attributes & 16) for attributes in rows)

def sid_size(sid):
    require(isinstance(sid,bytes) and len(sid)>=8 and sid[0]==1 and sid[1]<=15,'default_dacl_sid_shape')
    size=8+4*sid[1]
    require(len(sid)==size,'default_dacl_sid_length')
    return size

def acl_entries(raw):
    require(isinstance(raw,bytes) and 8<=len(raw)<=65535,'default_dacl_acl_shape')
    revision,_,size,count,_=struct.unpack_from('<BBHHH',raw)
    require(revision in (2,4) and size==len(raw),'default_dacl_acl_header')
    rows=[];offset=8
    for _ in range(count):
        require(offset+4<=size,'default_dacl_ace_header')
        ace_size=struct.unpack_from('<H',raw,offset+2)[0]
        require(ace_size>=4 and ace_size%4==0 and offset+ace_size<=size,'default_dacl_ace_bounds')
        rows.append(raw[offset:offset+ace_size]);offset+=ace_size
    return rows

def user_default_acl(raw,sid):
    """Preserve every old ACE; append only this token user's inheritable allow.

    Matches PostgreSQL17 AddUserToTokenDacl, not an OS/file/desktop ACL change.
    Existing deny ACEs remain ahead of the addition. NULL DACL is never allowed.
    """
    sid_size(sid);rows=acl_entries(raw)
    for ace in rows:
        if len(ace)>=8 and ace[0]==0 and not ace[1]&8 and struct.unpack_from('<I',ace,4)[0]&0x10000000 and ace[8:]==sid:
            return raw,False
    extra=struct.pack('<BBHI',0,1,8+len(sid),0x10000000)+sid
    revision,sbz,_,_,sbz2=struct.unpack_from('<BBHHH',raw)
    size=8+sum(map(len,rows))+len(extra)
    require(size<=65535 and len(rows)<65535,'default_dacl_growth_budget')
    return struct.pack('<BBHHH',revision,sbz,size,len(rows)+1,sbz2)+b''.join(rows)+extra,True

class Token:
    def __init__(self):
        require(os.name=='nt' and c.sizeof(c.c_void_p)==8, 'restricted_windows_x64')
        self._lock=threading.RLock()
        self.handle = None; self.sids = []
        self.a = c.WinDLL('advapi32',use_last_error=True)
        self.k = c.WinDLL('kernel32',use_last_error=True)
        bindings = {
            'OpenProcessToken':([w.HANDLE,w.DWORD,c.POINTER(w.HANDLE)],w.BOOL),
            'CreateRestrictedToken':([w.HANDLE,w.DWORD,w.DWORD,c.POINTER(SidAttr),w.DWORD,c.c_void_p,w.DWORD,c.c_void_p,c.POINTER(w.HANDLE)],w.BOOL),
            'ConvertStringSidToSidW':([w.LPCWSTR,c.POINTER(c.c_void_p)],w.BOOL),
            'EqualSid':([c.c_void_p,c.c_void_p],w.BOOL),
            'GetTokenInformation':([w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.POINTER(w.DWORD)],w.BOOL),
            'SetTokenInformation':([w.HANDLE,c.c_int,c.c_void_p,w.DWORD],w.BOOL),
            'LookupPrivilegeValueW':([w.LPCWSTR,w.LPCWSTR,c.POINTER(Luid)],w.BOOL),
            'CreateProcessAsUserW':([w.HANDLE,w.LPCWSTR,w.LPWSTR,c.c_void_p,c.c_void_p,w.BOOL,w.DWORD,c.c_void_p,w.LPCWSTR,c.c_void_p,c.c_void_p],w.BOOL)}
        for name,(args,ret) in bindings.items():
            f=getattr(self.a,name);f.argtypes=args;f.restype=ret
        for name,args,ret in [('GetCurrentProcess',[],w.HANDLE),('CloseHandle',[w.HANDLE],w.BOOL),
                              ('LocalFree',[c.c_void_p],c.c_void_p),('OpenProcess',[w.DWORD,w.BOOL,w.DWORD],w.HANDLE)]:
            f=getattr(self.k,name);f.argtypes=args;f.restype=ret
        original=w.HANDLE(); restricted=w.HANDLE()
        try:
            # QUERY | DUPLICATE | ASSIGN_PRIMARY | ADJUST_DEFAULT. Only the
            # derived token default is changed; no ADJUST_PRIVILEGES right.
            require(self.a.OpenProcessToken(self.k.GetCurrentProcess(),0x8B,c.byref(original)), 'open_own_token')
            user=self._user_sid(original)
            for value in ('S-1-5-32-544','S-1-5-32-547'):
                sid=c.c_void_p()
                require(self.a.ConvertStringSidToSidW(value,c.byref(sid)), 'well_known_sid')
                self.sids.append(sid)
            disabled=(SidAttr*2)(*(SidAttr(s,0) for s in self.sids))
            require(self.a.CreateRestrictedToken(original,1,2,disabled,0,None,0,None,c.byref(restricted)), 'create_restricted_token')
            self.handle=restricted
            require(self._user_sid(restricted)==user,'restricted_token_user_changed')
            default=self._ensure_user_default_acl(user)
            self.record=self.inspect(restricted)
            self.record['user_default_dacl']=default
        except BaseException:
            self.close();raise
        finally:
            if original:self.k.CloseHandle(original)

    def _metadata(self,handle,kind):
        # Keep info(8/2/21/3)'s established ABI rules unchanged. These two
        # explicit pointer-bearing classes have separate bounded validation.
        require(kind in (1,6),'default_dacl_metadata_class')
        size=w.DWORD();c.set_last_error(0)
        ok=self.a.GetTokenInformation(handle,kind,None,0,c.byref(size));error=c.get_last_error()
        header=c.sizeof(SidAttr) if kind==1 else c.sizeof(c.c_void_p)
        require(not ok and error==122 and header+8<=size.value<=1048576,'default_dacl_metadata_size')
        buf=(w.DWORD*((size.value+3)//4))();returned=w.DWORD();c.set_last_error(0)
        require(self.a.GetTokenInformation(handle,kind,buf,c.sizeof(buf),c.byref(returned)) and returned.value==size.value,'default_dacl_metadata_read')
        address=c.c_void_p.from_buffer(buf).value;base=c.addressof(buf)
        require(address is not None and base+header<=address<=base+returned.value-8,'default_dacl_inline_pointer')
        prefix=c.string_at(address,8)
        length=8+4*prefix[1] if kind==1 else struct.unpack_from('<H',prefix,2)[0]
        require(8<=length<=65535 and address+length<=base+returned.value,'default_dacl_inline_length')
        raw=c.string_at(address,length)
        if kind==1:sid_size(raw)
        else:acl_entries(raw)
        return raw

    def _user_sid(self,handle):return self._metadata(handle,1)

    def _ensure_user_default_acl(self,user):
        before=self._metadata(self.handle,6);after,added=user_default_acl(before,user)
        if added:
            storage=c.create_string_buffer(after);header=c.c_void_p(c.addressof(storage))
            require(self.a.SetTokenInformation(self.handle,6,c.byref(header),c.sizeof(header)),'set_restricted_default_dacl')
        actual=self._metadata(self.handle,6)
        require(actual==after,'restricted_default_dacl_readback')
        return {'user_sid_sha256':hashlib.sha256(user).hexdigest(),'current_user_allow_present':True,
            'user_ace_added':added,'before_sha256':hashlib.sha256(before).hexdigest(),'after_sha256':hashlib.sha256(actual).hexdigest(),
            'before_ace_count':len(acl_entries(before)),'after_ace_count':len(acl_entries(actual)),
            'old_aces_preserved':acl_entries(actual)[:len(acl_entries(before))]==acl_entries(before),
            'only_derived_token_default_modified':True,'OS_file_or_desktop_ACLs_modified':False}

    def info(self,handle,kind):
        require(kind in (8,2,21,3),'token_info_class_refused')
        size=w.DWORD();c.set_last_error(0)
        sized=self.a.GetTokenInformation(handle,kind,None,0,c.byref(size));error=c.get_last_error()
        require(not sized and error==122,'token_info_sizing:'+str(kind)+':'+str(error)+':'+str(size.value))
        require(valid_length(kind,size.value),'token_info_length:'+str(kind)+':'+str(size.value))
        # DWORD objects explicitly provide zero-initialization and >=DWORD
        # alignment even for the one-byte class-21 output. No smaller generic ABI.
        buf=(w.DWORD*((max(4,size.value)+3)//4))()
        returned=w.DWORD();c.set_last_error(0)
        ok=self.a.GetTokenInformation(handle,kind,buf,c.sizeof(buf),c.byref(returned));error=c.get_last_error()
        require(ok,'token_info_read:'+str(kind)+':'+str(error))
        require(returned.value==size.value and valid_length(kind,returned.value)
            and returned.value<=c.sizeof(buf),'token_info_return_length:'+str(kind)+':'+str(returned.value))
        require(c.addressof(buf)%4==0,'token_info_alignment')
        return TokenInfo(buf,kind,size.value,returned.value)

    def inspect(self,handle):
        token_type=self.info(handle,8)
        require(w.DWORD.from_buffer(token_type.buffer).value==1, 'primary_token_required')
        groups=self.info(handle,2);buf=groups.buffer;count=w.DWORD.from_buffer(buf).value
        require(Groups.Items.offset+count*c.sizeof(SidAttr)<=groups.returned, 'token_groups_shape')
        rows=(SidAttr*count).from_buffer(buf,Groups.Items.offset)
        relevant=[int(row.Attributes) for row in rows if any(self.a.EqualSid(row.Sid,s) for s in self.sids)]
        require(groups_ok(relevant), 'admin_power_group_not_deny_only')
        restrictions=self.info(handle,21)
        require(filtered_flag(restrictions), 'token_restrictions_required')
        allowed=Luid()
        require(self.a.LookupPrivilegeValueW(None,'SeChangeNotifyPrivilege',c.byref(allowed)), 'change_notify_luid')
        privilege_info=self.info(handle,3);p=privilege_info.buffer;n=w.DWORD.from_buffer(p).value
        require(Privileges.Items.offset+n*c.sizeof(Privilege)<=privilege_info.returned, 'token_privileges_shape')
        privileges=(Privilege*n).from_buffer(p,Privileges.Items.offset)
        require(all(not v.Attributes & 2 or (v.Luid.LowPart,v.Luid.HighPart)==(allowed.LowPart,allowed.HighPart) for v in privileges), 'unexpected_enabled_privilege')
        return {'primary':True,'administrative_groups_enabled':False,'deny_only_group_count':len(relevant),
                'has_restrictions':True,'only_change_notify_privilege_enabled':True,
                'token_information':[v.receipt() for v in (token_type,groups,restrictions,privilege_info)]}

    def process(self,pid):
        # The timeout thread may close the Job/token while a worker observes a
        # backend. Keep SID buffers/handles alive through the whole observation.
        with self._lock:
            require(self.handle is not None,'restricted_token_closed')
            return self._process(pid)

    def _process(self,pid):
        process=self.k.OpenProcess(0x1000,False,pid)
        require(process,'restricted_process_open')
        token=w.HANDLE()
        try:
            require(self.a.OpenProcessToken(process,8,c.byref(token)), 'restricted_process_token')
            result=self.inspect(token)
            user=self._user_sid(token);acl=self._metadata(token,6)
            _,needs_add=user_default_acl(acl,user)
            require(not needs_add,'actual_process_default_dacl_user_missing')
            result['default_dacl_user_allow_present']=True
            return result
        finally:
            if token:self.k.CloseHandle(token)
            self.k.CloseHandle(process)

    def close(self):
        with self._lock:
            if self.handle:self.k.CloseHandle(self.handle);self.handle=None
            for sid in self.sids:self.k.LocalFree(sid)
            self.sids=[]

class KernelProxy:
    def __init__(self,k,token):
        self._k,self._token=k,token
    def __getattr__(self,name):
        return getattr(self._k,name)
    def CreateProcessW(self,*args):
        # No post-create exception here: original _start must take ownership of
        # the returned handles before any additional validation can fail.
        return self._token.a.CreateProcessAsUserW(self._token.handle,*args)

class RestrictedJob(native.NativeJob):
    def __init__(self,plan):
        self.token=Token()
        try:
            super().__init__(plan)
            self.api.k=KernelProxy(self.api.k,self.token)
        except BaseException:
            self.token.close();raise

    def close(self,timeout=10):
        try:
            super().close(timeout)
        finally:
            self.token.close()
