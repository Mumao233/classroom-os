"""Fixed Batch4 guest boundary. No configurable database or executable target."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import sys
import zipfile

sys.dont_write_bytecode = True
HOME = Path(r'C:\Users\Administrator\PR03bBatch4Attempt08')
PRIOR = Path(r'C:\Users\Administrator\PR03bBatch4')
PRIOR04 = Path(r'C:\Users\Administrator\PR03bBatch4Attempt04')
_prior04_entry_digest = None
KIT = Path(r'C:\Users\Administrator\PR03bLabBasic')
OLD = Path(r'C:\Users\Administrator\PR03bNetworkContinuation03')
ROOT = KIT / 'checkout'
BINDING = '210f9d961522ac5a7104c086fb210c698688da2242a2abceed5b970e6a0887b1'
SYSTEM_RECORD = 'a6384460be775cbbe65077572486de964c81e98613d8fe8424a9c8720b7804c6'
MANIFEST_PIN = '4c34bea1b0dcb4f5f10c158e50461da397fc369e8f3825f5f34f5e4309f43074'
GUARD_PIN = '2a5535619651ac4835374c0777edff26f5759f9038de27c3edd4fcfc3d998a1c'
PRESERVED_PIN = '55a9e74fe678532b087e56238b869d182b90c3d775517ebc99ba15528d7d2305'
RESUME_PIN = '43743f0e5fecdb89cfd930bc02c8ed2283d8798155fa7ab9b2e7c9cc5fc10f43'
PRIOR_PINS = {'BUNDLE-MANIFEST.json':'8340100c840f4b48e8681e1dab6bb96abdec7a19de90e5df8fc992d53c69e214',
    'STARTED.json':'c628b14b9d1d31e21bd274bee2554e3c24990f40d51fd584097544b04799e874',
    'STOP.json':'cc78e5527d82d58724e43f44fd02810a540cb41660f12c0d669781f19d7c8cba',
    'SELFTEST.json':'6a1210935224f7e5d1ee236a593902c6fb45baede3842603c9502be4634eb790'}

class Stop(RuntimeError):
    pass

def require(value, code):
    if not value:
        raise Stop(code)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def read(path, limit=16*1024*1024):
    path = Path(path)
    for part in (*reversed(path.parents), path):
        s = part.lstat()
        require(not stat.S_ISLNK(s.st_mode) and not getattr(s, 'st_file_attributes', 0) & 0x400, 'reparse_refused')
    a = path.stat()
    require(stat.S_ISREG(a.st_mode) and a.st_nlink == 1 and a.st_size <= limit, 'regular_single_link_required')
    with path.open('rb') as f:
        b = os.fstat(f.fileno()); raw = f.read(limit+1)
    c = path.stat()
    keys = ('st_dev','st_ino','st_size','st_mtime_ns','st_nlink')
    require(len(raw) <= limit and all(getattr(a,k)==getattr(b,k)==getattr(c,k) for k in keys), 'file_changed')
    return raw

def strict(raw):
    def pairs(items):
        d = {}
        for k,v in items:
            require(k not in d, 'duplicate_json_key'); d[k] = v
        return d
    def bad(_):
        raise Stop('nonfinite_json')
    def floating(value):
        number=float(value)
        require(math.isfinite(number),'nonfinite_json');return number
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=bad,parse_float=floating)

def write(path, obj):
    with Path(path).open('x', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2); f.write('\n')

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, 'module_loader_missing')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def bundle():
    raw = read(HOME/'BUNDLE-MANIFEST.json')
    m = strict(raw)
    require(m['schema_version']==1 and set(m)=={'schema_version','files'}, 'bundle_schema')
    require(set(m['files']) == {'common.py','schema.py','cases.py','runner.py','restricted_process.py',
        'selftest.py','coordinator.ps1','START-IN-GUEST.ps1','read_only_guard.ps1','approved-inputs.json','prior-attempt04.json','resume-inputs.json','README.txt'}, 'bundle_members')
    for name,digest in m['files'].items():
        require(re.fullmatch(r'[a-zA-Z0-9_.-]+', name) and sha(read(HOME/name)) == digest, 'bundle_changed')
    return sha(raw)

def frozen_guest_path(value):
    # Authority is the SHA-pinned manifest, not a guessed folder-name prefix.
    # Validate local canonical spelling before the unchanged no-reparse reader.
    require(isinstance(value,str) and not any(ord(c)<32 or ord(c)==127 for c in value)
        and not any(c in value for c in ('/','<','>','"','|','?','*'))
        and not any(p in ('.','..','') for p in value.split('\\')[1:])
        and not any(p.endswith((' ','.')) for p in value.split('\\')[1:])
        and ':' not in value[2:],'preserved_path_spelling')
    p=PureWindowsPath(value)
    require(p.is_absolute() and p.drive=='C:' and str(p)==value
        and p.is_relative_to(PureWindowsPath(r'C:\Users\Administrator')),'preserved_local_path')
    return Path(value)

def prior_attempt():
    raw_pins={}
    for name,digest in PRIOR_PINS.items():
        raw_pins[name]=read(PRIOR/name)
        require(sha(raw_pins[name])==digest,'prior_batch4_stop_changed')
    m=strict(raw_pins['BUNDLE-MANIFEST.json'])
    require(set(m)=={'schema_version','files'} and m['schema_version']==1,'prior_manifest_schema')
    expected=set(m['files'])|set(PRIOR_PINS)
    require({p.name for p in PRIOR.iterdir()}==expected and len(expected)==15,'prior_batch4_file_set_changed')
    for name,digest in m['files'].items():
        require(re.fullmatch(r'[A-Za-z0-9_.-]+',name) and sha(read(PRIOR/name))==digest,'prior_batch4_source_changed')
    old_test=strict(raw_pins['SELFTEST.json'])
    require(old_test['passed'] is True and old_test['tests']==15 and old_test['failures']==old_test['errors']==old_test['skipped']==0,'prior_selftest_differs')
    require({p.name for p in PRIOR.iterdir()}==expected,'prior_batch4_file_set_changed')
    return {'directory':str(PRIOR),'files':15,'unchanged':True,'new_target_resumed':False}

def prior_attempt04():
    """Archive-pinned 04 files plus the console-verified final receipt semantics.

    The final ENTRY-RESULT was written after ZIP creation, so its archived raw
    hash is unknown. Admit only the exact known JSON meaning, then pin its raw
    bytes for this entire 05 process. This never grants database execution.
    """
    global _prior04_entry_digest
    pins=strict(read(HOME/'prior-attempt04.json'))
    require(pins['home']==str(PRIOR04) and pins['run_id']=='ebe473b6dfee4a518f13ab4456508a21','prior04_binding')
    names=set(pins['files'])
    require(len(names)==25 and 'evidence/results.json' in names,'prior04_pin_set')
    hashes={}
    for name,info in pins['files'].items():
        require(name=='evidence/results.json' or re.fullmatch(r'[A-Za-z0-9_.-]+',name),'prior04_member')
        raw=read(PRIOR04/name)
        require(len(raw)==info['bytes'] and sha(raw)==info['sha256'],'prior04_evidence_changed')
        hashes[name]=info['sha256']
    expected={n.split('/')[0] for n in names}|{'ENTRY-RESULT.json'}
    require({p.name for p in PRIOR04.iterdir()}==expected and len(expected)==26,'prior04_file_set_changed')
    require({p.name for p in (PRIOR04/'evidence').iterdir()}=={'results.json'},'prior04_evidence_set_changed')
    raw=read(PRIOR04/'ENTRY-RESULT.json');value=strict(raw)
    canonical=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
    require(sha(canonical)==pins['entry_result_canonical_sha256'],'prior04_entry_result_differs')
    digest=sha(raw)
    if _prior04_entry_digest is None:_prior04_entry_digest=digest
    require(digest==_prior04_entry_digest,'prior04_entry_bytes_changed_since_admission')
    return {'directory':str(PRIOR04),'known_raw_files':25,'known_hashes':hashes,'entry_result_semantics_verified':True,
        'entry_result_raw_sha256_at_admission':digest,'prior04_run_retired':True,'new_target_resumed':False}

def original_inputs():
    pins = strict(read(HOME/'approved-inputs.json'))
    for name,info in pins['old_files'].items():
        require(re.fullmatch(r'[A-Za-z0-9_.-]+',name), 'old_member')
        raw = read(OLD/name)
        require(sha(raw)==info['sha256'] and len(raw)==info['bytes'], 'approved_input_changed')
    require(sha(read(OLD/'SYSTEM-POWERSHELL.json'))==SYSTEM_RECORD, 'system_record_changed')
    require(sha(read(HOME/'read_only_guard.ps1'))==GUARD_PIN, 'os_guard_changed')
    preserved_raw=read(OLD/'PRESERVED-INPUTS.json')
    require(sha(preserved_raw)==PRESERVED_PIN,'preservation_manifest_pin')
    preserved = strict(preserved_raw)
    for item in (*preserved['files'],*pins['approved_case_files']):
        p=frozen_guest_path(item['path'])
        raw=read(p)
        require(len(raw)==item['bytes'] and sha(raw)==item['sha256'], 'prior_evidence_changed')
    for item in preserved['directories']:
        p=frozen_guest_path(item['path'])
        require(not p.is_symlink(),'preserved_directory_link')
        require(sorted(x.name for x in p.iterdir())==sorted(item['members']), 'prior_evidence_set_changed')
    prior_attempt()
    prior_attempt04()
    resume_inputs()
    return pins

def resume_inputs():
    """Protect failed05/06, maintenance and diagnosis; no DB authority."""
    raw=read(HOME/'resume-inputs.json')
    require(sha(raw)==RESUME_PIN,'resume_manifest_changed')
    pins=strict(raw)
    expected={r'C:\Users\Administrator\PR03bBatch4Attempt05',
        r'C:\Users\Administrator\PR03bBatch4RasMaintenance01',
        r'C:\Users\Administrator\PR03bBatch4RasMaintenance02',
        r'C:\Users\Administrator\PR03bBatch4Attempt06',
        r'C:\Users\Administrator\PR03bBatch4InitdbDiagnostics01',
        r'C:\Users\Administrator\PR03bBatch4Attempt07'}
    dirs=pins['directories']
    require(pins['schema_version']==1 and pins['database_execution_allowed'] is False
        and len(dirs)==6 and {d['home'] for d in dirs}==expected,'resume_directory_binding')
    count=0
    for item in dirs:
        home=frozen_guest_path(item['home']);files=item['files'];allowed_dirs=set()
        require(type(files) is dict and 0<len(files)<=128,'resume_file_budget')
        for name,v in files.items():
            require(name=='evidence/results.json' or name not in ('.','..')
                and re.fullmatch(r'[A-Za-z0-9_.-]+',name),'resume_member')
            if '/' in name:allowed_dirs.add('evidence')
            data=read(home/name)
            require(len(data)==v['bytes'] and sha(data)==v['sha256'],'resume_file_changed')
        seen=set();todo=[home]
        while todo:
            for p in todo.pop().iterdir():
                entry=p.lstat();name=p.relative_to(home).as_posix()
                require(not stat.S_ISLNK(entry.st_mode) and not getattr(entry,'st_file_attributes',0)&0x400,'resume_reparse_refused')
                if stat.S_ISDIR(entry.st_mode):
                    require(name in allowed_dirs,'resume_directory_set_changed');todo.append(p)
                else:
                    require(name in files,'resume_file_set_changed');seen.add(name)
        require(seen==set(files),'resume_file_set_changed');count+=len(files)
    return {'directories':6,'files':count,'sha256':RESUME_PIN,'database_execution_allowed':False}

def verify_dependencies():
    """Compare installed import files against original, hash-pinned wheel bytes."""
    manifest = strict(read(KIT/'PAYLOAD-MANIFEST.json'))
    require(sha(read(KIT/'PAYLOAD-MANIFEST.json')) == MANIFEST_PIN, 'kit_manifest')
    site = ROOT/'work/pr03b/guest-venv/Lib/site-packages'
    expected = {}
    wheels = sorted((KIT/'wheels').glob('*.whl'))
    require(len(wheels)==6, 'six_wheels_required')
    for wheel in wheels:
        name = wheel.relative_to(KIT).as_posix()
        raw = read(wheel,64*1024*1024)
        require(sha(raw)==manifest['files'][name]['sha256'], 'wheel_pin')
        with zipfile.ZipFile(wheel) as z:
            for item in z.infolist():
                if item.is_dir(): continue
                require(not item.filename.startswith('/') and '..' not in Path(item.filename).parts, 'wheel_member')
                if '.data/' in item.filename or item.filename.endswith('.dist-info/RECORD'): continue
                expected[item.filename] = sha(z.read(item))
    for name,digest in expected.items():
        require(sha(read(site/name,64*1024*1024))==digest, 'installed_dependency_changed')
    # Extra .py/.pyd/DLL files inside importable package trees cannot shadow a pin.
    roots = {Path(n).parts[0] for n in expected if not Path(n).parts[0].endswith('.dist-info')}
    for name in roots:
        anchor = site/name
        if anchor.is_dir():
            for p in anchor.rglob('*'):
                if p.is_file() and p.suffix.lower() in ('.py','.pyd','.dll'):
                    require(p.relative_to(site).as_posix() in expected, 'extra_import_file')
    # -B prevents writes but still permits reading an existing __pycache__.
    # Use a never-existing private cache prefix so imports use the verified
    # wheel-matched source/binary files, rather than mutable installed .pyc.
    cache=HOME/'no-bytecode-cache'
    require(not cache.exists(),'private_bytecode_prefix_must_be_absent')
    sys.pycache_prefix=str(cache)
    sys.path.append(str(site))
    return {'wheels':6,'installed_files_verified':len(expected)}

def bootstrap():
    require(os.name=='nt' and sys.version_info[:3]==(3,12,14) and sys.flags.isolated==1
        and sys.flags.no_site==1 and sys.flags.dont_write_bytecode==1, 'fixed_private_python_flags')
    require(Path(__file__).resolve().parent==HOME and Path(sys.executable)==KIT/'python/python.exe', 'guest_only')
    require(not any(k.upper().startswith('PG') or k.upper()=='CLASSROOM_WEB_DATABASE_URL' for k in os.environ), 'database_environment_refused')
    digest = bundle(); original_inputs()
    approved = load('_b4_approved', OLD/'approved_net_probe.py')
    original = approved.verify_original()  # only verify; no old main/probe
    dependencies = verify_dependencies()
    sys.path.insert(0,str(ROOT/'tools/pr03b'))
    import target_guard, native_process, pg_launcher, pg_probe
    require(target_guard.ROOT==ROOT and pg_launcher.ROOT==ROOT, 'original_root_binding')
    wrapper = load('_b4_approved_wrapper', OLD/'continue_probe.py')
    system = wrapper._system_executable()
    require(system['identity_sha256']==SYSTEM_RECORD, 'system_image_binding')
    return target_guard,native_process,pg_launcher,pg_probe,wrapper,{
        'bundle_sha256':digest,'original':original,'dependencies':dependencies,'system':system,
        'prior_attempt':prior_attempt(),'prior_attempt04':prior_attempt04(),'resume_inputs':resume_inputs()}
