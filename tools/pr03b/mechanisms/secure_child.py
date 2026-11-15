"""Fixed restricted child: secrets arrive only over one protected local pipe."""
import sys,json,time
print('{"phase":"python_started"}',flush=True)
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import common as b
print('{"phase":"common_loaded"}',flush=True)
b.runtime_dependencies();sys.path.insert(0,str(b.BASE));sys.path.insert(0,str(b.ROOT/'tools/pr03b'))
print('{"phase":"dependencies_verified"}',flush=True)
import windows,crypto,hashlib,json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
print('{"phase":"crypto_loaded"}',flush=True)
from ctypes import wintypes as w
import ctypes as c
def main():
    b.require(len(sys.argv)==3 and sys.argv[1].startswith(r'\\.\pipe\PR03b-S3-') and sys.argv[2].isdigit(),'fixed_pipe_arguments')
    windows.protect_process()
    k,a=windows.api();b.require(k.WaitNamedPipeW(sys.argv[1],5000),'pipe_wait')
    handle=k.CreateFileW(sys.argv[1],0xc0000000,0,None,3,0,None)
    b.require(handle not in (None,c.c_void_p(-1).value),'pipe_client_open')
    try:
        pid=w.ULONG();b.require(k.GetNamedPipeServerProcessId(handle,c.byref(pid)) and pid.value==int(sys.argv[2]),'pipe_server_instance')
        print('{"phase":"pipe_connected"}',flush=True)
        k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE
        c.set_last_error(0);other=k.OpenProcess(0x10,False,int(sys.argv[2]));memory_denied=not bool(other) and c.get_last_error()==5
        if other:k.CloseHandle(other)
        windows.send(k,handle,{'kind':'hello','declared_subject':'forged-Administrator'})
        packet=windows.receive(k,handle);signer=Ed25519PrivateKey.from_private_bytes(crypto.unb64(packet['private'],32)) if packet['private'] is not None else None;root=Path(packet['D0']);independent=None
        if packet['D0_material'] is not None:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            from cryptography.hazmat.primitives import serialization
            b.require(root==b.HOME/'D0-private','fixed_installer_D0_root')
            installed=b.strict(b.read(root/'installed-root.json'));manifest=b.strict(b.read(root/'installed.json'));ledger=b.strict(b.read(root/'ledger.json'));pin=Ed25519PublicKey.from_public_bytes(crypto.unb64(installed['root_public'],32))
            material=packet['D0_material']
            def verify(value):
                body=value['current'];pin.verify(crypto.unb64(value['signature'],64),crypto.signed('classroom.identity.current.v1',body))
                b.require(installed['os_subject']==windows.sid_string() and installed['deployment']==manifest['deployment']==body['deployment'],'installed_mapping')
                b.require(manifest.get('enabled') is True and body.get('enabled') is True,'D0_revoked')
                b.require(body.get('phase')==ledger['phase']=='ready','D0_pending_transition')
                b.require(crypto.b64(signer.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw))==manifest['recover_public'],'registered_recover_key')
                b.require(body['nonce']==value['nonce'] and body['phase']==ledger['phase']=='ready' and value['pg']==[ledger['version'],ledger['hash']]==[manifest['version'],hashlib.sha256(crypto.jcs(manifest)).hexdigest()]==[body['version'],body['hash']],'D0_cross_process_mismatch')
            try:verify(material)
            except b.Stop as ex:
                if str(ex) not in ('D0_revoked','D0_pending_transition'):raise
                windows.send(k,handle,{'D0_rejected':str(ex),'signature_emitted':False});return 0
            count=0
            for v in (dict(material,nonce='old-nonce'),dict(material,pg=[1,'0'*64]),dict(material,current=dict(material['current'],phase='reserved'))):
                try:verify(v)
                except Exception:count+=1
            independent={'positive':True,'mismatches_denied':count}
        denied=False
        try:(root/'UNAUTHORIZED').write_bytes(b'not-secret')
        except PermissionError:denied=True
        changed=False
        try:windows.protect_d0(root)
        except b.Stop:changed=True
        windows.send(k,handle,{'signature':crypto.b64(signer.sign(crypto.signed('classroom.identity.authenticate.v1',packet['challenge']))),
            'digest':hashlib.sha256(packet['marker'].encode()).hexdigest(),'D0_write_denied':denied,'D0_ACL_change_denied':changed,'parent_VM_read_denied':memory_denied,'independent_D0_checks':independent})
    finally:k.CloseHandle(handle)
    print(json.dumps({'passed':True,'secrets_in_stdout':False}));return 0
if __name__=='__main__':
    try:code=main()
    except BaseException as ex:print(json.dumps({'passed':False,'error_type':type(ex).__name__,'safe_error':str(ex) if isinstance(ex,b.Stop) else 'sanitized'}));code=1
    raise SystemExit(code)
