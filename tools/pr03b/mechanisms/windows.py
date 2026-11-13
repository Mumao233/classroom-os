"""Guest-owned jobs, local ACL pipe and synthetic D0 directory only."""
from __future__ import annotations
import ctypes as c
from ctypes import wintypes as w
import hashlib,json,os,secrets,threading,time
from pathlib import Path
import common as b
def api():
    b.require(os.name=='nt','Windows_guest_required')
    k=c.WinDLL('kernel32',use_last_error=True);a=c.WinDLL('advapi32',use_last_error=True)
    for lib,name,args,ret in ((a,'ConvertStringSecurityDescriptorToSecurityDescriptorW',[w.LPCWSTR,w.DWORD,c.POINTER(c.c_void_p),c.POINTER(w.DWORD)],w.BOOL),
        (a,'SetFileSecurityW',[w.LPCWSTR,w.DWORD,c.c_void_p],w.BOOL),(k,'LocalFree',[c.c_void_p],c.c_void_p),
        (k,'CreateNamedPipeW',[w.LPCWSTR,w.DWORD,w.DWORD,w.DWORD,w.DWORD,w.DWORD,w.DWORD,c.c_void_p],w.HANDLE),
        (k,'ConnectNamedPipe',[w.HANDLE,c.c_void_p],w.BOOL),(k,'ReadFile',[w.HANDLE,c.c_void_p,w.DWORD,c.POINTER(w.DWORD),c.c_void_p],w.BOOL),
        (k,'WriteFile',[w.HANDLE,c.c_void_p,w.DWORD,c.POINTER(w.DWORD),c.c_void_p],w.BOOL),
        (k,'CloseHandle',[w.HANDLE],w.BOOL),(k,'GetNamedPipeClientProcessId',[w.HANDLE,c.POINTER(w.ULONG)],w.BOOL),
        (k,'GetNamedPipeServerProcessId',[w.HANDLE,c.POINTER(w.ULONG)],w.BOOL),(k,'CreateFileW',[w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE],w.HANDLE),
        (k,'WaitNamedPipeW',[w.LPCWSTR,w.DWORD],w.BOOL),(k,'DisconnectNamedPipe',[w.HANDLE],w.BOOL),
        (k,'CancelIoEx',[w.HANDLE,c.c_void_p],w.BOOL)):
        f=getattr(lib,name);f.argtypes=args;f.restype=ret
    return k,a
def sid_string():
    # Read TokenUser directly; a secret receiver must not import the PG
    # launcher/SQL dependency graph merely to obtain its own kernel identity.
    k,a=api();k.GetCurrentProcess.argtypes=[];k.GetCurrentProcess.restype=w.HANDLE
    a.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,c.POINTER(w.HANDLE)];a.OpenProcessToken.restype=w.BOOL
    a.GetTokenInformation.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.POINTER(w.DWORD)];a.GetTokenInformation.restype=w.BOOL
    a.ConvertSidToStringSidW.argtypes=[c.c_void_p,c.POINTER(w.LPWSTR)];a.ConvertSidToStringSidW.restype=w.BOOL
    token=w.HANDLE();b.require(a.OpenProcessToken(k.GetCurrentProcess(),8,c.byref(token)),'own_sid_token')
    try:
        size=w.DWORD();ok=a.GetTokenInformation(token,1,None,0,c.byref(size))
        b.require(not ok and c.get_last_error()==122 and c.sizeof(c.c_void_p)+8<=size.value<=65536,'own_sid_sizing')
        data=(w.DWORD*((size.value+3)//4))();returned=w.DWORD()
        b.require(a.GetTokenInformation(token,1,data,c.sizeof(data),c.byref(returned)) and returned.value==size.value,'own_sid_read')
        address=c.c_void_p.from_buffer(data).value;base=c.addressof(data)
        b.require(address is not None and base+c.sizeof(c.c_void_p)<=address<=base+returned.value-8,'own_sid_pointer')
        prefix=c.string_at(address,8);b.require(prefix[0]==1 and prefix[1]<=15 and address+8+4*prefix[1]<=base+returned.value,'own_sid_bounds')
        text=w.LPWSTR();b.require(a.ConvertSidToStringSidW(address,c.byref(text)),'own_sid_string')
        try:return text.value
        finally:k.LocalFree(c.cast(text,c.c_void_p))
    finally:k.CloseHandle(token)
def protect_process():
    k,a=api();security=c.c_void_p();size=w.DWORD()
    b.require(a.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;RC;;;OW)',1,c.byref(security),c.byref(size)),'process_descriptor')
    k.GetCurrentProcess.argtypes=[];k.GetCurrentProcess.restype=w.HANDLE
    a.SetKernelObjectSecurity.argtypes=[w.HANDLE,w.DWORD,c.c_void_p];a.SetKernelObjectSecurity.restype=w.BOOL
    try:b.require(a.SetKernelObjectSecurity(k.GetCurrentProcess(),4,security),'process_dump_access_DACL')
    finally:k.LocalFree(security)
    k.SetErrorMode.argtypes=[w.UINT];k.SetErrorMode.restype=w.UINT;k.SetErrorMode(0x0001|0x0002|0x8000)
    wer=k;wer.WerSetFlags.argtypes=[w.DWORD];wer.WerSetFlags.restype=w.LONG
    wer.WerGetFlags.argtypes=[w.HANDLE,c.POINTER(w.DWORD)];wer.WerGetFlags.restype=w.LONG
    b.require(wer.WerSetFlags(0x21)==0,'WER_no_heap_no_UI');flags=w.DWORD();b.require(wer.WerGetFlags(k.GetCurrentProcess(),c.byref(flags))==0 and flags.value&0x21==0x21,'WER_flags_readback')
    import faulthandler,sys
    b.require(not faulthandler.is_enabled() and sys.gettrace() is None and sys.getprofile() is None,'no_trace_profile_fault_dump')
    return {'process_VM_read_denied_to_unprivileged_peer':True,'WER_NOHEAP':True,'WER_NO_UI':True,'python_trace_or_faulthandler':False,'malicious_Administrator_outside_boundary':True}
def protect_d0(path):
    path=Path(path);b.require(path.is_relative_to(b.HOME) and path.is_dir(),'D0_new_stage_path_required')
    k,a=api();security=c.c_void_p();size=w.DWORD()
    # OWNER RIGHTS suppress implicit WRITE_DAC; BA is deny-only in the child.
    sddl='D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;RC;;;OW)(A;OICI;GRGX;;;'+sid_string()+')'
    b.require(a.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,c.byref(security),c.byref(size)),'D0_descriptor')
    try:b.require(a.SetFileSecurityW(str(path),0x80000004,security),'D0_directory_ACL')
    finally:k.LocalFree(security)

def protect_private(path):
    """Coordinator-only new secret directory; reduced TokenUser gets no read."""
    path=Path(path);b.require(path.is_relative_to(b.HOME) and path.is_dir(),'new_private_stage_path')
    k,a=api();security=c.c_void_p();size=w.DWORD()
    b.require(a.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;RC;;;OW)',1,c.byref(security),c.byref(size)),'private_descriptor')
    try:b.require(a.SetFileSecurityW(str(path),0x80000004,security),'private_directory_ACL')
    finally:k.LocalFree(security)
def start_python(plan,script,arguments,env,label):
    from restricted_process import RestrictedJob
    executable=b.KIT/'python/python.exe';script=Path(script)
    b.require(script.parent==b.HOME and script.name in b.strict(b.read(b.HOME/'BUNDLE-MANIFEST.json'))['files'],'fixed_child_script')
    b.require(all(type(v) is str and len(v)<512 for v in arguments),'nonsecret_child_arguments')
    job=RestrictedJob(plan)
    if script.name in ('load_driver.py','graded_driver.py'):
        import resource_group
        b.require(resource_group.CURRENT is not None,'load_client_requires_hard_resource_group')
        job.api.k=resource_group.ClientCreation(job.api.k)
    receipt={'phase':'creating','label':label,'fixed_script':script.name}
    try:
        identity=job._start((str(executable),'-X','utf8','-I','-S','-B',str(script),*arguments),executable,b.sha(b.read(executable,64*1024*1024)),env,plan.run_root/(label+'.stdout.log'),plan.run_root/(label+'.stderr.log'))
        receipt.update(phase='created',pid=identity.pid,creation_token=identity.creation_token)
        b.require(job.token.process(identity.pid)['administrative_groups_enabled'] is False,'child_must_be_restricted')
        if script.name in ('load_driver.py','graded_driver.py'):receipt['resource_group']=resource_group.CURRENT.client(job,identity.pid)
        else:
            import resource_group
            if resource_group.CURRENT is not None:
                b.require(resource_group.CURRENT.contains(identity.pid),'SUT_child_resource_membership')
                receipt['SUT_hard_resource_group_member']=True
        return job,identity
    except BaseException as ex:
        receipt.update(error_type=type(ex).__name__,exited_before_cleanup=job.wait(0),exit_code_before_cleanup=job.exit_code());job.close();raise
    finally:b.write(plan.run_root/(label+'.start-receipt.json'),receipt)
class Security(c.Structure):_fields_=[('length',w.DWORD),('descriptor',c.c_void_p),('inherit',w.BOOL)]
def send(k,handle,value):
    raw=json.dumps(value,separators=(',',':')).encode();b.require(len(raw)<60000,'pipe_message_budget');written=w.DWORD()
    b.require(k.WriteFile(handle,raw,len(raw),c.byref(written),None) and written.value==len(raw),'pipe_write')
def receive(k,handle):
    buf=c.create_string_buffer(65536);n=w.DWORD();b.require(k.ReadFile(handle,buf,len(buf),c.byref(n),None) and 0<n.value<60000,'pipe_read')
    return b.strict(buf.raw[:n.value])
def ipc_experiment(plan,env,check,label='secure-channel',d0=None,pg=None,expect_revoked=False,expect_reserved=False):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    import crypto
    import re
    revoked_label=re.fullmatch(r'D0-revoked-(0[0-9]|1[0-9])',label) is not None
    reserved_label=re.fullmatch(r'D0-reserved-(0[0-9]|1[0-9])',label) is not None
    restart_label=re.fullmatch(r'D0-restart-ready-(0[0-9]|1[0-9])',label) is not None
    b.require(type(expect_reserved) is bool and not (expect_revoked and expect_reserved),'fixed_child_closed_control')
    b.require(label in ('secure-channel','native-probe','D0-verifier') or (revoked_label and expect_revoked) or (reserved_label and expect_reserved) or (restart_label and not expect_reserved and not expect_revoked),'fixed_IPC_label')
    b.require(type(expect_revoked) is bool and (not expect_revoked or (d0 is not None and revoked_label and d0.manifest['enabled'] is False)),'fixed_revoked_child_control')
    b.require(not expect_reserved or (d0 is not None and reserved_label and d0.highwater['phase']=='reserved'),'fixed_pending_child_control')
    closed_reason='D0_revoked' if expect_revoked else 'D0_pending_transition' if expect_reserved else None
    if d0 is None:root=b.HOME/('D0-ACL-'+label);root.mkdir();protect_d0(root)
    else:root=d0.root;b.require(root==b.HOME/'D0-private','installed_D0_fixed_path')
    k,a=api();sd=c.c_void_p();size=w.DWORD();sddl='D:P(A;;GA;;;SY)(A;;GA;;;'+sid_string()+')'
    b.require(a.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,c.byref(sd),c.byref(size)),'pipe_descriptor')
    security=Security(c.sizeof(Security),sd,False);name=r'\\.\pipe\PR03b-S3-'+secrets.token_hex(16)
    handle=k.CreateNamedPipeW(name,3|0x80000,4|2|8,1,65536,65536,3000,c.byref(security))
    k.LocalFree(sd);b.require(handle not in (None,c.c_void_p(-1).value),'pipe_create')
    ready=threading.Event();done=threading.Event();box={};expected=[];job=None
    signer=d0.recover if d0 else Ed25519PrivateKey.generate();challenge={'purpose':'synthetic-authority','nonce':crypto.b64(secrets.token_bytes(32))};marker=secrets.token_hex(32)
    def server():
        try:
            connected=k.ConnectNamedPipe(handle,None)
            b.require(connected or c.get_last_error()==535,'pipe_connect');b.require(ready.wait(5),'owned_client_not_ready')
            pid=w.ULONG();b.require(k.GetNamedPipeClientProcessId(handle,c.byref(pid)) and pid.value==expected[0] and job.contains(pid.value),'pipe_kernel_peer_PID')
            job.token.process(pid.value)
            request=receive(k,handle);b.require(request['kind']=='hello','pipe_hello')
            material=None
            if d0:
                nonce=crypto.b64(secrets.token_bytes(32));body,sig=d0.current(nonce)
                material={'nonce':nonce,'current':body,'signature':crypto.b64(sig),'pg':list(pg)}
            send(k,handle,{'private':None if closed_reason else crypto.b64(signer.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption())),
                'challenge':challenge,'marker':None if closed_reason else marker,'D0':str(root),'D0_material':material})
            response=receive(k,handle)
            if closed_reason:
                b.require(response=={'D0_rejected':closed_reason,'signature_emitted':False},'closed_child_must_not_sign')
                box.update(passed=True,closed_child_refused=closed_reason,secret_key_delivered=False);return
            signer.public_key().verify(crypto.unb64(response['signature'],64),crypto.signed('classroom.identity.authenticate.v1',challenge))
            b.require(response['digest']==hashlib.sha256(marker.encode()).hexdigest(),'pipe_marker_integrity')
            box.update(passed=True,client_pid=pid.value,caller_claim_ignored=request['declared_subject']!='kernel-peer',D0_write_denied=response['D0_write_denied'],D0_ACL_change_denied=response['D0_ACL_change_denied'],parent_VM_read_denied=response['parent_VM_read_denied'],independent_D0_checks=response['independent_D0_checks'])
        except BaseException as ex:box.update(passed=False,error_type=type(ex).__name__,safe_error=str(ex) if isinstance(ex,b.Stop) else 'sanitized')
        finally:done.set()
    t=threading.Thread(target=server,daemon=True);t.start()
    try:
        job,identity=start_python(plan,b.HOME/'secure_child.py',[name,str(os.getpid())],env,label)
        expected.append(identity.pid);ready.set();b.require(done.wait(30),'pipe_startup_watchdog')
        check('independent_closed_child_exact_refusal' if closed_reason else 'secret_named_pipe_kernel_peer_signature',box.get('passed') is True,{'error_type':box.get('error_type'),'safe_error':box.get('safe_error')})
        if closed_reason:check('independent_child_refuses_before_key_delivery:'+closed_reason,box.get('closed_child_refused')==closed_reason and box.get('secret_key_delivered') is False)
        else:
            check('D0_restricted_process_cannot_write',box.get('D0_write_denied') is True)
            check('D0_owner_cannot_rewrite_ACL',box.get('D0_ACL_change_denied') is True)
            check('secret_broker_process_memory_not_peer_readable',box.get('parent_VM_read_denied') is True)
            check('pipe_client_claim_is_not_identity',box.get('caller_claim_ignored') is True)
            if d0:check('D01_reduced_process_fixed_root_signature_mapping_mirror',box.get('independent_D0_checks')=={'positive':True,'mismatches_denied':3})
        b.require(job.wait(5) and job.exit_code()==0,'secret_child_exit')
        for suffix in ('stdout','stderr'):
            raw=b.read(plan.run_root/(label+'.'+suffix+'.log'))
            check('secret_not_child_'+suffix,marker.encode() not in raw and crypto.b64(signer.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption())).encode() not in raw)
    finally:
        if job is not None:b.write(plan.run_root/(label+'.finish-receipt.json'),{'exit_observed_before_cleanup':job.wait(0),'exit_code_before_cleanup':job.exit_code(),'server_done':done.is_set(),'safe_server_result':box})
        k.CancelIoEx(handle,None);k.DisconnectNamedPipe(handle);k.CloseHandle(handle)
        if job is not None:job.close();check('secret_child_job_cleanup',job.cleanup_confirmed is True)
    return {'actual_kernel_peer':True,'pipe_remote_clients_rejected':True,'pipe_handle_inheritable':False,'raw_secrets_exported':False}
