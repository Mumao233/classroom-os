"""Single-stage disposable mechanism lab, never a production auth entrypoint."""
from __future__ import annotations
import hashlib,importlib.util,json,os,sys
from pathlib import Path
HOME=Path(r'C:\Users\Administrator\PR03bS3Mechanisms32')
BASE=Path(r'C:\Users\Administrator\PR03bBatch4Attempt08')
ASSETS=Path(r'C:\Users\Administrator\PR03bS3Assets01')
KIT=Path(r'C:\Users\Administrator\PR03bLabBasic');ROOT=KIT/'checkout'
OLD=Path(r'C:\Users\Administrator\PR03bNetworkContinuation03')
RESUME_PIN='43743f0e5fecdb89cfd930bc02c8ed2283d8798155fa7ab9b2e7c9cc5fc10f43'
class Stop(RuntimeError):pass
def require(ok,code):
    if not ok:raise Stop(code)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def strict(raw):
    def pairs(rows):
        d={}
        for k,v in rows:require(k not in d,'duplicate_json');d[k]=v
        return d
    def bad(v):raise Stop('nonfinite_json')
    return json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=bad)
def write(p,v):
    with Path(p).open('x',encoding='utf-8') as f:json.dump(v,f,indent=2);f.write('\n')
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m
    exec(compile(Path(path).read_bytes(),str(path),'exec'),m.__dict__);return m
_base=None
_source_pin=None
def foundation():
    global _base
    if _base is None:
        path=BASE if os.name=='nt' and BASE.exists() else Path(__file__).resolve().parents[1]/'batch4'
        require(sha((path/'common.py').read_bytes())=='226ba3fe8931111b05a1e891b6aeeba3881ccafb08eaf6234b5ac11bf1af9c6f','approved_foundation_common_pin')
        _base=load('_s3_approved_foundation',path/'common.py')
    return _base
def read(p,limit=16*1024*1024):return foundation().read(p,limit)
def preserved_read_limit(name,pin):
    if name=='evidence/native-events.jsonl':return 256*1024*1024
    if name=='coordinator.stderr.log':
        require(type(pin) is dict and set(pin)=={'bytes','sha256'} and type(pin['bytes']) is int and 0<=pin['bytes']<=128*1024*1024,'preserved_stderr_read_budget')
        return 128*1024*1024
    return 16*1024*1024
def bundle():
    if _source_pin is not None:return _source_pin.verify()
    m=strict(read(HOME/'BUNDLE-MANIFEST.json'))
    require(m['schema_version']==1 and set(m)=={'schema_version','files'},'stage_manifest_shape')
    for name,digest in m['files'].items():
        require('/' not in name and '\\' not in name and name not in ('.','..'),'stage_member')
        require(sha(read(HOME/name,256*1024*1024))==digest,'stage_source_changed')
    return sha(read(HOME/'BUNDLE-MANIFEST.json'))
def close_source_pin():
    global _source_pin
    if _source_pin is not None:_source_pin.close();_source_pin=None
def original_inputs():
    base=foundation();base.bundle();base.original_inputs()
    pins=strict(read(HOME/'approved08.json'))
    require(pins['home']==str(BASE),'approved08_home')
    seen=set()
    for p in BASE.rglob('*'):
        if p.is_file():
            name=p.relative_to(BASE).as_posix();raw=read(p)
            require(name in pins['files'] and pins['files'][name]=={'bytes':len(raw),'sha256':sha(raw)},'approved08_changed');seen.add(name)
    require(seen==set(pins['files']),'approved08_file_set')
    prior=strict(read(HOME/'prior-stage.json'))
    for entry in prior['directories']:
        root=Path(entry['home']);require(root.parent==HOME.parent and root.name.startswith('PR03bS3Mechanisms') and root!=HOME,'prior_stage_path')
        seen=set()
        for p in root.rglob('*'):
            if p.is_file():
                name=p.relative_to(root).as_posix()
                require(name in entry['files'],'prior_stage_changed')
                # Historical logs are never truncated or omitted. The one
                # oversized stopped stderr has a fixed 128MiB read ceiling;
                # regular/single-link/no-reparse and complete byte/hash checks
                # still use the original reader. This grants no execution.
                raw=read(p,preserved_read_limit(name,entry['files'][name]))
                require(entry['files'][name]=={'bytes':len(raw),'sha256':sha(raw)},'prior_stage_changed');seen.add(name)
        require(seen==set(entry['files']),'prior_stage_set')
    return pins
def dependencies(imports_only=False):
    raw=read(ASSETS/'ASSETS-MANIFEST.json')
    require(sha(raw)=='05ce07184444ae996c5bb7f81af1465025cba92735caf1a0f4d6e4b5be2e6756','assets_manifest_pin')
    m=strict(raw);root=ASSETS
    pins={n:v for n,v in m['files'].items() if not imports_only or n.startswith('deps/')}
    for name,pin in pins.items():
        p=root/name;require(p.is_relative_to(root),'dependency_path')
        raw=read(p,384*1024*1024);require(sha(raw)==pin,'dependency_changed')
    if imports_only:require({p.relative_to(root).as_posix() for p in (root/'deps').rglob('*') if p.is_file()}==set(pins),'dependency_import_set')
    else:require(dependency_members({p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()},set(m['files'])),'dependency_set')
    sys.path[:0]=[str(root/'deps')]
    return {'files':len(m['files']),'manifest_sha256':sha(read(ASSETS/'ASSETS-MANIFEST.json'))}
def dependency_members(seen,pinned):return seen==pinned|{'ASSETS-MANIFEST.json'}
def runtime_dependencies():
    # Parent bootstrap hashes every file before launch. The installer has made
    # the tree read-only to this reduced token; no library is accepted solely
    # because a writable, self-supplied manifest names it.
    raw=read(ASSETS/'ASSETS-MANIFEST.json')
    require(sha(raw)=='05ce07184444ae996c5bb7f81af1465025cba92735caf1a0f4d6e4b5be2e6756','runtime_assets_pin')
    # CRT open maps Win32 ACCESS_DENIED to errno EACCES without winerror.
    # Inspect the native result directly; no successful writable handle is kept.
    import ctypes as c
    from ctypes import wintypes as w
    k=c.WinDLL('kernel32',use_last_error=True)
    k.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE];k.CreateFileW.restype=w.HANDLE
    k.CloseHandle.argtypes=[w.HANDLE];k.CloseHandle.restype=w.BOOL
    c.set_last_error(0);handle=k.CreateFileW(str(ASSETS/'ASSETS-MANIFEST.json'),0x40000000,7,None,3,0,None);error=c.get_last_error()
    if handle not in (None,c.c_void_p(-1).value):k.CloseHandle(handle);raise Stop('runtime_dependency_tree_writable')
    require(error==5,'runtime_readonly_not_access_denied')
    sys.path[:0]=[str(ASSETS/'deps')]
    return {'parent_full_hash_verification':True,'runtime_manifest_pin':True,'reduced_token_write_denied':True}
def bootstrap():
    require(os.name=='nt' and Path(__file__).resolve().parent==HOME and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode,'stage_guest_only')
    digest=bundle();original_inputs()
    guard,native,legacy,probe,wrapper,verified=foundation().bootstrap()
    verified['approved08_bundle_sha256']=verified['bundle_sha256'];verified['bundle_sha256']=digest
    verified['stage_dependencies']=dependencies()
    import epoch_policy
    wrapper=epoch_policy.bind(wrapper)
    verified['current_boot_binding']={'sha256':epoch_policy.RECORD_SHA,'boot_utc':epoch_policy.BOOT,'original_policy_code_object_unchanged':True,'old_plan_unchanged':True}
    global _source_pin
    require(_source_pin is None,'source_pin_one_coordinator_only')
    from source_pin import Bundle
    _source_pin=Bundle(HOME,digest)
    verified['source_pin']={'files':len(_source_pin.files),'ancestors':len(_source_pin.directories),'all_bytes_rehashed_at_every_boundary':True,'noninheritable':True,'sharing_denials':_source_pin.deny_probe()}
    return guard,native,legacy,probe,wrapper,verified
