"""Synthetic selfchecks only. Audit hook forbids socket/process/native execution."""
from __future__ import annotations
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import ast
import copy
import ctypes
from ctypes import wintypes
from contextlib import contextmanager, redirect_stdout
import hashlib
import io
import json
import struct
import tempfile
import threading
from types import SimpleNamespace,ModuleType
import unittest
from unittest.mock import patch
import common as b
import schema as s
import cases
import runner

def audit(event,args):
    if event.startswith(('socket.','subprocess.')) or event in ('os.system','os.posix_spawn','ctypes.dlopen','ctypes.call_function'):
        raise RuntimeError('synthetic_selftest_execution_refused')

class SqlError(Exception):
    def __init__(self,state):self.sqlstate=state

class FakeSql:
    def __init__(self,state='42501',changed=False):self.state,self.changed,self.events=state,changed,[]
    def execute(self,sql,params=None):
        self.events.append(sql)
        if sql=='attack':
            if self.state:raise SqlError(self.state)
        return self
    def fetchone(self):
        changed=self.changed and self.events.count('SELECT identity_api.lab_observe()')>1
        return ('b'*32 if changed else 'a'*32,)

class Checks(unittest.TestCase):
    def test_no_network_audit(self):
        import socket
        with self.assertRaises(RuntimeError):socket.socket()

    def test_no_child_audit(self):
        import subprocess
        with self.assertRaises(RuntimeError):subprocess.run(['not-executed'])

    def test_render_exact_roles(self):
        for run in ('1'*32,'a'*32,'0'*32):
            with self.subTest(run=run):
                sql=s.install(run)
                self.assertEqual(sql.count('CREATE ROLE '),23)
                self.assertEqual(sql.count('ENABLE ROW LEVEL SECURITY'),9)
                self.assertEqual(sql.count('FORCE ROW LEVEL SECURITY'),9)
                self.assertEqual(sql.count('CREATE FUNCTION '),10)
                self.assertNotIn('GRANT ALL ',sql)
                self.assertNotIn('IF NOT EXISTS',sql)
                self.assertNotIn('BYPASSRLS;',sql)
                self.assertNotIn('CREATE EXTENSION',sql)
                self.assertNotIn('CREATE DATABASE',sql)

    def test_invalid_run_ids(self):
        for run in ('','A'*32,'a'*31,'a'*33,'../','a'*31+"'",None,12):
            with self.subTest(run=run):
                with self.assertRaises(ValueError):s.logins(run)

    def test_identifier_injection(self):
        for x in ('a;b','a"','a.b','a--','A','a'*64,'1role'):
            with self.subTest(value=x):
                with self.assertRaises(ValueError):s.ident(x)

    def test_role_names_unique_and_short(self):
        roles=s.logins('a'*32)
        self.assertEqual(len(set(roles.values())),12)
        self.assertTrue(all(len(v)<64 for v in roles.values()))
        self.assertEqual(len(set(s.OWNERS.values())),8)
        self.assertNotIn(s.TABLE_OWNER,s.OWNERS.values())

    def test_no_sql_password_or_scope_cli(self):
        tree=ast.parse(Path(runner.__file__).read_text(encoding='utf-8'))
        self.assertIn("sys.argv[1:]!=['fixed-run']",Path(runner.__file__).read_text(encoding='utf-8'))
        self.assertNotIn('argparse',ast.unparse(tree))
        self.assertNotIn('input(',ast.unparse(tree))

    def test_error_accepts_only_exact_sqlstate(self):
        fake=ModuleType('psycopg');fake.Error=SqlError
        old=sys.modules.get('psycopg');sys.modules['psycopg']=fake
        try:
            for state,changed,ok in [('42501',False,True),('23503',False,False),(None,False,False),
                                     ('57014',False,False),('08006',False,False),('42501',True,False)]:
                with self.subTest(state=state,changed=changed):
                    c=FakeSql(state,changed)
                    if ok:
                        self.assertEqual(cases.expect_error(c,'attack','42501')['observed_sqlstate'],'42501')
                    else:
                        with self.assertRaises(b.Stop):cases.expect_error(c,'attack','42501')
                    self.assertIn('ROLLBACK TO SAVEPOINT denied',c.events)
                    self.assertIn('RELEASE SAVEPOINT denied',c.events)
        finally:
            if old is None:sys.modules.pop('psycopg',None)
            else:sys.modules['psycopg']=old

    def test_identity_mutations(self):
        plan=SimpleNamespace(database='db',port=6543,server_version_num=170011)
        row=('role','role','db','127.0.0.1',6543,170011,False,False,False,False,False,False)
        self.assertTrue(runner.role_identity_ok(row,plan,'role'))
        for i in range(len(row)):
            with self.subTest(column=i):
                bad=list(row);bad[i]=True if i>=6 else 'wrong'
                self.assertFalse(runner.role_identity_ok(tuple(bad),plan,'role'))
        for bad in (None,(),row+(False,)):
            self.assertFalse(runner.role_identity_ok(bad,plan,'role'))

    def test_strict_json(self):
        for raw in (b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":Infinity}',b'{"a":1e999}'):
            with self.subTest(raw=raw):
                with self.assertRaises(b.Stop):b.strict(raw)

    def test_frozen_evidence_paths_and_bootstrap_error_receipt(self):
        approved=(r'C:\Users\Administrator\PR03bNetworkIsolation\cases\run-123\STOP.json',
            r'C:\Users\Administrator\PR03bPreflightDiagnostics01\capture\original.stderr.bin',
            r'C:\Users\Administrator\PR03bPreflightDiagnostics01\diagnose.py')
        for path in approved:
            with self.subTest(path=path):self.assertEqual(str(b.frozen_guest_path(path)),path)
        for path in ('',None,r'\\server\share\file',r'D:\file',r'C:\Users\Administrator2\file',
            r'C:\Users\Administrator\..\other',r'C:\Users\Administrator\x:stream',r'C:/Users/Administrator/x',
            'C:\\Users\\Administrator\\x\\',r'C:\Users\Administrator\.\x',
            'C:\\Users\\Administrator\\x\n',r'C:\Users\Administrator\x.',r'C:\Users\Administrator\x?'):
            with self.subTest(path=path):
                with self.assertRaises(b.Stop):b.frozen_guest_path(path)
        # Exercise the actual manifest gate: a legitimate diagnostic path is
        # accepted only in the pinned manifest; tampering fails before its read.
        name=r'C:\Users\Administrator\PR03bPreflightDiagnostics01\capture\original.stderr.bin'
        payload=b'fixture'
        good=json.dumps({'files':[{'path':name,'bytes':len(payload),'sha256':b.sha(payload)}],'directories':[]}).encode()
        bad=good.replace(b'original.stderr.bin',b'changed.stderr.bin')
        for raw,ok in ((good,True),(bad,False)):
            reads=[]
            def fake_read(path,*unused):
                reads.append(str(path))
                if path==b.HOME/'approved-inputs.json':return b'{"old_files":{},"approved_case_files":[]}'
                if path==b.OLD/'SYSTEM-POWERSHELL.json':return b'{}'
                if path==b.HOME/'read_only_guard.ps1':return b'#fixture'
                if path==b.OLD/'PRESERVED-INPUTS.json':return raw
                if str(path)==name:return payload
                raise AssertionError('unexpected fixture read')
            with patch.object(b,'read',fake_read),patch.object(b,'SYSTEM_RECORD',b.sha(b'{}')),patch.object(b,'GUARD_PIN',b.sha(b'#fixture')),patch.object(b,'PRESERVED_PIN',b.sha(good)),patch.object(b,'prior_attempt',lambda:None),patch.object(b,'prior_attempt04',lambda:None),patch.object(b,'resume_inputs',lambda:None):
                if ok:b.original_inputs();self.assertIn(name,reads)
                else:
                    with self.assertRaisesRegex(b.Stop,'preservation_manifest_pin'):b.original_inputs()
                    self.assertNotIn(name,reads)
        old_args=sys.argv;old_bootstrap=b.bootstrap;capture=io.StringIO()
        def fail():raise b.Stop('preserved_local_path')
        try:
            sys.argv=['runner.py','fixed-run'];b.bootstrap=fail
            with redirect_stdout(capture):self.assertEqual(runner.main(),1)
            report=json.loads(capture.getvalue())
            self.assertEqual(report['code'],'preserved_local_path')
            self.assertEqual(report['phase'],'bootstrap_or_entry')
            self.assertFalse(report['database_execution_allowed'])
        finally:sys.argv=old_args;b.bootstrap=old_bootstrap

    def test_resume_old_attempt_and_maintenance_protection(self):
        # Controlled host cwd is work/; exact guest invocation uses its fixed HOME.
        root=b.HOME if Path(__file__).resolve().parent==b.HOME else Path.cwd()
        homes=(r'C:\Users\Administrator\PR03bBatch4Attempt05',
            r'C:\Users\Administrator\PR03bBatch4RasMaintenance01',r'C:\Users\Administrator\PR03bBatch4RasMaintenance02',
            r'C:\Users\Administrator\PR03bBatch4Attempt06',r'C:\Users\Administrator\PR03bBatch4InitdbDiagnostics01',r'C:\Users\Administrator\PR03bBatch4Attempt07')
        for kind in ('positive','changed','missing','extra_file','extra_dir','bad_pin','wrong_home'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory(prefix='b4-resume-',dir=root) as directory:
                base=Path(directory);config=base/'config';config.mkdir();mapping={};directories=[]
                for i,home in enumerate(homes):
                    p=base/str(i);p.mkdir();(p/'STOP.json').write_bytes(b'fixture');mapping[home]=p
                    directories.append({'home':home,'files':{'STOP.json':{'bytes':7,'sha256':b.sha(b'fixture')}}})
                if kind=='wrong_home':directories[0]['home']=r'C:\Users\Administrator\unapproved'
                raw=json.dumps({'schema_version':1,'database_execution_allowed':False,'directories':directories}).encode()
                (config/'resume-inputs.json').write_bytes(raw)
                if kind=='changed':(base/'0/STOP.json').write_bytes(b'changed')
                if kind=='missing':(base/'0/STOP.json').unlink()
                if kind=='extra_file':(base/'0/EXTRA.json').write_bytes(b'{}')
                if kind=='extra_dir':(base/'0/extra').mkdir()
                with patch.object(b,'HOME',config),patch.object(b,'RESUME_PIN','0'*64 if kind=='bad_pin' else b.sha(raw)),patch.object(b,'frozen_guest_path',lambda x:mapping[x]):
                    if kind=='positive':self.assertEqual(b.resume_inputs()['files'],6)
                    else:
                        with self.assertRaises((b.Stop,FileNotFoundError)):b.resume_inputs()

    def test_snapshot_failure_diagnostics_never_grant_admission(self):
        raw=json.dumps({'passed':False,'stage':'original_isolation','failure':{'code':'adapter_set_changed','error_type':'RuntimeException','untrusted':'not exported'}}).encode()
        for stdout,expected in ((raw,True),(b'not json',False),(raw+b' '*1024*1024,False),
                (b'{"passed":false,"stage":"x","failure":{"code":"x"},"passed":true}',False),
                (b'{"passed":false,"stage":"x","failure":{"code":"unsafe message text"}}',True)):
            with self.subTest(size=len(stdout)):
                result=runner.snapshot_command_evidence(SimpleNamespace(returncode=1,stdout=stdout,stderr=b''),'synthetic')
                self.assertEqual('failure' in result,expected)
                self.assertEqual(result['returncode'],1)
                self.assertNotIn('passed',result);self.assertNotIn('database_execution_allowed',result)
                if 'failure' in result:self.assertNotIn('untrusted',result['failure'])

    def test_owned_helper_native_result_capture(self):
        identity=SimpleNamespace(pid=123,creation_token=456,executable=Path('C:/synthetic/initdb.exe'))
        for code in (0,1,0xc0000142,259,None,False,-1,0x100000000):
            with self.subTest(code=code):
                events=[];row={}
                job=SimpleNamespace(start_helper=lambda *args:events.append('start') or identity,
                    wait=lambda seconds:events.append(('wait',seconds)) or True,
                    exit_code=lambda:events.append('exit') or code)
                if type(code) is int and 0<=code<=0xffffffff:
                    if code==0:
                        value=runner.run_owned_helper(job,'initdb',[], 'a'*64,{},Path('C:/synthetic'),lambda *args:b'OK',row)
                        self.assertEqual(value,'OK');self.assertEqual(row['stage'],'complete')
                    else:
                        with self.assertRaisesRegex(b.Stop,'restricted_helper_exit:0x'):
                            runner.run_owned_helper(job,'initdb',[], 'a'*64,{},Path('C:/synthetic'),lambda *args:b'not read',row)
                    self.assertEqual(row['exit_code'],code);self.assertEqual(row['exit_code_hex'],f'0x{code:08x}')
                    self.assertTrue(row['exit_observed_before_cleanup'])
                else:
                    with self.assertRaisesRegex(b.Stop,'restricted_helper_exit_unavailable'):
                        runner.run_owned_helper(job,'initdb',[], 'a'*64,{},Path('C:/synthetic'),lambda *args:b'',row)
                    self.assertIsNone(row['exit_code']);self.assertFalse(row['exit_observed_before_cleanup'])
                self.assertEqual(row['created_process']['pid'],123);self.assertEqual(row['created_process']['creation_token'],456)
                self.assertEqual(events,['start',('wait',30),'exit'])
        events=[];row={}
        job=SimpleNamespace(start_helper=lambda *args:identity,wait=lambda seconds:False,
            exit_code=lambda:events.append('should not read exit after timeout'))
        with self.assertRaisesRegex(b.Stop,'restricted_helper_deadline'):
            runner.run_owned_helper(job,'initdb',[], 'a'*64,{},Path('C:/synthetic'),lambda *args:b'',row)
        self.assertEqual(events,[]);self.assertFalse(row['wait_completed']);self.assertIsNone(row['exit_code'])

    def test_actual_helper_cleanup_preserves_pre_cleanup_exit(self):
        # Compile the actual nested helper into a synthetic enclosing scope.
        # No native API, executable, file or socket is opened by this harness.
        tree=ast.parse(Path(runner.__file__).read_text(encoding='utf-8'))
        fixed=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='fixed_run')
        helper=next(n for n in fixed.body if isinstance(n,ast.FunctionDef) and n.name=='helper')
        harness=ast.parse('''def harness():
    helpers_ok=True
    commands=[]
    try:
        helper('initdb',[])
    except b.Stop as error:
        failure=str(error)
    else:
        failure=None
    return helpers_ok,commands,failure
''')
        harness.body[0].body.insert(2,copy.deepcopy(helper));ast.fix_missing_locations(harness)
        for cleanup_ok in (True,False):
            with self.subTest(cleanup_ok=cleanup_ok):
                events=[]
                class FakeJob:
                    def __init__(self,plan):
                        self.token=SimpleNamespace(record={'synthetic':True});self.cleanup_confirmed=False;self.code=0xc0000142
                    def start_helper(self,*args):return SimpleNamespace(pid=123,creation_token=456,executable=Path('C:/synthetic/initdb.exe'))
                    def wait(self,seconds):return True
                    def exit_code(self):events.append('exit_observed');return self.code
                    def close(self):events.append('close');self.code=1;self.cleanup_confirmed=cleanup_ok
                namespace={'b':b,'RestrictedJob':FakeJob,'plan':SimpleNamespace(run_root=Path('C:/synthetic')),
                    'legacy':SimpleNamespace(_tool=lambda name:Path('C:/synthetic')/name),'env':{},
                    'guard':SimpleNamespace(_read_file=lambda *args:b'fixture'),'run_owned_helper':runner.run_owned_helper}
                exec(compile(harness,'<synthetic actual helper>','exec'),namespace)
                ok,commands,failure=namespace['harness']()
                self.assertEqual(events,['exit_observed','close']);self.assertEqual(ok,cleanup_ok)
                self.assertEqual(len(commands),1);self.assertEqual(commands[0]['exit_code'],0xc0000142)
                self.assertEqual(commands[0]['exit_code_hex'],'0xc0000142');self.assertEqual(commands[0]['cleanup_confirmed'],cleanup_ok)
                self.assertEqual(failure,'restricted_helper_exit:0xc0000142')

    def test_transaction_guards(self):
        for statement,wanted in [('BEGIN READ WRITE',2),('BEGIN READ ONLY',2),('COMMIT',2),('ROLLBACK',2),
                                 ('SELECT 1',0),('ROLLBACK TO SAVEPOINT denied',0)]:
            with self.subTest(statement=statement):
                events=[]
                c=SimpleNamespace(execute=lambda *a,**k:events.append('execute'))
                lease=SimpleNamespace(_local=lambda:events.append('guard'))
                runner.FixedConnection(c,lease).execute(statement)
                self.assertEqual(events.count('guard'),wanted)
                if wanted:self.assertEqual(events,['guard','execute','guard'])

    def test_guard_failure_prevents_sql(self):
        def deny():raise b.Stop('denied')
        events=[]
        with self.assertRaises(b.Stop):
            runner.FixedConnection(SimpleNamespace(execute=lambda *a,**k:events.append(1)),SimpleNamespace(_local=deny)).execute('BEGIN READ WRITE')
        self.assertEqual(events,[])

    def test_configuration_restricted_hba(self):
        run='a'*32
        plan=SimpleNamespace(port=6543,run_id=run,database='pr02a_'+run,login='pr02a_'+run+'_bootstrap',data_directory=Path('C:/synthetic/data'))
        files=runner.configuration(plan)
        hba=files['pg_hba.conf'].splitlines()
        self.assertEqual(len(hba),5)
        self.assertTrue(all('127.0.0.1/32 scram-sha-256' in v for v in hba[:2]))
        self.assertEqual(hba[-3:],['host all all 0.0.0.0/0 reject','host all all ::0/0 reject','local all all reject'])
        self.assertIn("default_transaction_read_only=on",files['postgresql.conf'])
        self.assertIn("log_min_error_statement=panic",files['postgresql.conf'])
        self.assertIn("log_parameter_max_length_on_error=0",files['postgresql.conf'])

    def test_source_imports_and_no_native_start_override(self):
        # Import adapter against a fake base; no native API instance is created.
        fake=ModuleType('native_process');fake.NativeJob=type('FakeJob',(),{})
        old=sys.modules.get('native_process');sys.modules['native_process']=fake
        try:
            r=b.load('_batch4_synthetic_restricted',Path(__file__).parent/'restricted_process.py')
            for rows,valid in [([],True),([16],True),([16,16],True),([4],False),([20],False),([0],False)]:
                with self.subTest(rows=rows):self.assertEqual(r.groups_ok(rows),valid)
            calls=[]
            token=SimpleNamespace(handle='restricted',a=SimpleNamespace(CreateProcessAsUserW=lambda *args:calls.append(args) or True))
            base=SimpleNamespace(other='unchanged')
            proxy=r.KernelProxy(base,token)
            self.assertTrue(proxy.CreateProcessW('exact','args'))
            self.assertEqual(calls,[('restricted','exact','args')])
            self.assertEqual(proxy.other,'unchanged')
            self.assertNotIn('_start',r.RestrictedJob.__dict__)
            self.assertNotIn('start_postgres',r.RestrictedJob.__dict__)
            with self.assertRaises(RuntimeError):r.Token()
            # Token interpretation is exercised against bounded memory buffers,
            # with no Windows API call or native process in the host selftest.
            def token_fixture(group=16,token_type=1,restricted=1,privilege=23,enabled=2):
                token=object.__new__(r.Token);token.sids=[100,200]
                g=ctypes.create_string_buffer(r.Groups.Items.offset+ctypes.sizeof(r.SidAttr))
                wintypes.DWORD.from_buffer(g).value=1
                item=r.SidAttr.from_buffer(g,r.Groups.Items.offset);item.Sid=100;item.Attributes=group
                p=ctypes.create_string_buffer(r.Privileges.Items.offset+ctypes.sizeof(r.Privilege))
                wintypes.DWORD.from_buffer(p).value=1
                v=r.Privilege.from_buffer(p,r.Privileges.Items.offset);v.Luid.LowPart=privilege;v.Attributes=enabled
                def scalar(value):
                    buf=ctypes.create_string_buffer(4);wintypes.DWORD.from_buffer(buf).value=value;return buf
                boolean=(wintypes.DWORD*1)();ctypes.c_ubyte.from_buffer(boolean).value=restricted
                data={2:r.TokenInfo(g,2,len(g),len(g)),3:r.TokenInfo(p,3,len(p),len(p)),
                    8:r.TokenInfo(scalar(token_type),8,4,4),21:r.TokenInfo(boolean,21,1,1)}
                token.info=lambda handle,kind:data[kind]
                def lookup(a,b,pointer):
                    ctypes.cast(pointer,ctypes.POINTER(r.Luid)).contents.LowPart=23;return True
                token.a=SimpleNamespace(EqualSid=lambda a,b:a==b,LookupPrivilegeValueW=lookup)
                return token
            self.assertFalse(token_fixture().inspect('fake')['administrative_groups_enabled'])
            for mutation in ({'group':4},{'group':20},{'group':0},{'token_type':2},{'restricted':0},{'privilege':20}):
                with self.subTest(token=mutation):
                    with self.assertRaises(b.Stop):token_fixture(**mutation).inspect('fake')
            self.assertTrue(token_fixture(privilege=20,enabled=0).inspect('fake')['only_change_notify_privilege_enabled'])
            for kind,short in ((2,r.Groups.Items.offset),(3,r.Privileges.Items.offset)):
                token=token_fixture();saved=token.info
                def truncated(handle,k):
                    value=saved(handle,k)
                    return r.TokenInfo(value.buffer,k,short,short) if k==kind else value
                token.info=truncated
                with self.assertRaises(b.Stop):token.inspect('fake')
            token=object.__new__(r.Token);token._lock=threading.RLock();token.handle='owned';token.sids=[100,200]
            cleanup=[];token.k=SimpleNamespace(CloseHandle=lambda h:cleanup.append(('handle',h)),LocalFree=lambda sid:cleanup.append(('sid',sid)))
            token.close();token.close()
            self.assertEqual(cleanup,[('handle','owned'),('sid',100),('sid',200)])
            with self.assertRaises(b.Stop):token.process(123)
        finally:
            if old is None:sys.modules.pop('native_process',None)
            else:sys.modules['native_process']=old

    def test_token_info_class_specific_native_abi(self):
        fake=ModuleType('native_process');fake.NativeJob=type('FakeJob',(),{})
        old=sys.modules.get('native_process');sys.modules['native_process']=fake
        try:
            r=b.load('_batch4_abi_synthetic',Path(__file__).parent/'restricted_process.py')
            def fixture(kind=21,needed=1,returned=None,probe_error=122,read_ok=True,value=1,poison=True):
                calls=[]
                def query(handle,k,buffer,capacity,length):
                    if buffer is None:
                        ctypes.cast(length,ctypes.POINTER(wintypes.DWORD)).contents.value=needed
                        ctypes.set_last_error(probe_error);calls.append(('size',k,capacity));return False
                    calls.append(('read',k,capacity))
                    view=(ctypes.c_ubyte*capacity).from_buffer(buffer)
                    view[0]=value
                    if poison and capacity>=4:
                        for i in range(1,4):view[i]=0xA5
                    ctypes.cast(length,ctypes.POINTER(wintypes.DWORD)).contents.value=needed if returned is None else returned
                    ctypes.set_last_error(0 if read_ok else 5);return read_ok
                token=object.__new__(r.Token);token.a=SimpleNamespace(GetTokenInformation=query)
                return token,calls
            token,calls=fixture();info=token.info('synthetic',21)
            self.assertTrue(r.filtered_flag(info))
            self.assertEqual(calls,[('size',21,0),('read',21,4)])
            self.assertEqual(info.receipt(),{'class':21,'required_length':1,'returned_length':1,'capacity':4,'dword_aligned':True,'sizing_error':122,'read_success':True})
            token,_=fixture(value=0);self.assertFalse(r.filtered_flag(token.info('synthetic',21)))
            token,_=fixture(value=2)
            with self.assertRaises(b.Stop):r.filtered_flag(token.info('synthetic',21))
            for kwargs in ({'needed':0},{'needed':2},{'needed':4},{'needed':5},{'probe_error':5},
                {'probe_error':0},{'read_ok':False},{'returned':0},{'returned':2},{'returned':4},{'returned':5},
                {'needed':1024*1024+1}):
                with self.subTest(**kwargs):
                    token,_=fixture(**kwargs)
                    with self.assertRaises(b.Stop):token.info('synthetic',21)
            for kind,needed,ok in ((8,4,True),(8,1,False),(2,r.Groups.Items.offset,True),
                    (2,r.Groups.Items.offset-1,False),(3,r.Privileges.Items.offset,True),(3,1,False),(7,4,False)):
                with self.subTest(kind=kind,needed=needed):
                    token,_=fixture(kind=kind,needed=needed,poison=False)
                    if ok:self.assertEqual(token.info('synthetic',kind).returned,needed)
                    else:
                        with self.assertRaises(b.Stop):token.info('synthetic',kind)
        finally:
            if old is None:sys.modules.pop('native_process',None)
            else:sys.modules['native_process']=old

    def test_default_dacl_preserves_denies_and_only_adds_current_user(self):
        fake=ModuleType('native_process');fake.NativeJob=type('FakeJob',(),{})
        with patch.dict(sys.modules,{'native_process':fake}):
            r=b.load('_batch4_dacl_pure',Path(__file__).parent/'restricted_process.py')
        user=bytes.fromhex('010100000000000515000000')
        other=bytes.fromhex('010100000000000512000000')
        def ace(kind,sid,mask=0x10000000,flags=0):return struct.pack('<BBHI',kind,flags,8+len(sid),mask)+sid
        def acl(rows):return struct.pack('<BBHHH',2,0,8+sum(map(len,rows)),len(rows),0)+b''.join(rows)
        for rows in ([],[ace(1,user)],[ace(1,user),ace(0,other)],[ace(0,user,flags=8)],
                     [ace(0,user,mask=0x80000000)]):
            with self.subTest(rows=rows):
                raw=acl(rows);after,added=r.user_default_acl(raw,user)
                self.assertTrue(added);self.assertEqual(r.acl_entries(after),rows+[ace(0,user,flags=1)])
                self.assertEqual(r.user_default_acl(after,user),(after,False))
        raw=acl([ace(0,user)]);self.assertEqual(r.user_default_acl(raw,user),(raw,False))
        for bad in (b'',b'\0'*8,struct.pack('<BBHHH',2,0,12,1,0)+b'\0\0\x08\0',
                    struct.pack('<BBHHH',2,0,12,1,0)+b'\0\0\x05\0',
                    struct.pack('<BBHHH',2,0,8,1,0)):
            with self.subTest(bad=bad):
                with self.assertRaises(b.Stop):r.user_default_acl(bad,user)
        for sid in (b'',b'\0'*12,bytes([1,16])+b'\0'*70,user+b'\0'):
            with self.subTest(sid=sid):
                with self.assertRaises(b.Stop):r.user_default_acl(acl([]),sid)

    def test_default_dacl_native_pointer_bounds(self):
        fake=ModuleType('native_process');fake.NativeJob=type('FakeJob',(),{})
        with patch.dict(sys.modules,{'native_process':fake}):
            r=b.load('_batch4_dacl_metadata',Path(__file__).parent/'restricted_process.py')
        for kind in (1,6):
            payload=bytes.fromhex('010100000000000515000000') if kind==1 else struct.pack('<BBHHH',2,0,8,0,0)
            header=ctypes.sizeof(r.SidAttr) if kind==1 else ctypes.sizeof(ctypes.c_void_p)
            size=header+len(payload)
            for mode in ('ok','null','before','past','truncated','short_return','read_fail','bad_probe'):
                with self.subTest(kind=kind,mode=mode):
                    def query(handle,k,buffer,capacity,length):
                        ctypes.cast(length,ctypes.POINTER(wintypes.DWORD)).contents.value=size
                        if buffer is None:ctypes.set_last_error(5 if mode=='bad_probe' else 122);return False
                        base=ctypes.addressof(buffer);address=base+header
                        if mode=='null':address=None
                        if mode=='before':address=base
                        if mode=='past':address=base+size
                        if mode=='truncated':address=base+size-8
                        ctypes.c_void_p.from_buffer(buffer).value=address
                        ctypes.memmove(base+header,payload,len(payload))
                        if mode=='truncated':ctypes.memmove(base+size-8,b'\x01\x0f\xff\xff\0\0\0\0',8)
                        if mode=='short_return':ctypes.cast(length,ctypes.POINTER(wintypes.DWORD)).contents.value=size-1
                        return mode!='read_fail'
                    token=object.__new__(r.Token);token.a=SimpleNamespace(GetTokenInformation=query)
                    if mode=='ok':self.assertEqual(token._metadata('derived',kind),payload)
                    else:
                        with self.assertRaises(b.Stop):token._metadata('derived',kind)

    def test_default_dacl_only_derived_token_set_and_readback_required(self):
        fake=ModuleType('native_process');fake.NativeJob=type('FakeJob',(),{})
        with patch.dict(sys.modules,{'native_process':fake}):
            r=b.load('_batch4_dacl_set',Path(__file__).parent/'restricted_process.py')
        user=bytes.fromhex('010100000000000515000000');before=struct.pack('<BBHHH',2,0,8,0,0)
        after,_=r.user_default_acl(before,user)
        for mode in ('ok','set_fail','readback_mismatch','already_present'):
            with self.subTest(mode=mode):
                token=object.__new__(r.Token);token.handle='derived';calls=[];reads=[]
                def metadata(handle,kind):
                    reads.append((handle,kind))
                    return after if mode=='already_present' or len(reads)>1 and mode!='readback_mismatch' else before
                def setter(handle,kind,header,size):
                    address=ctypes.cast(header,ctypes.POINTER(ctypes.c_void_p)).contents.value
                    calls.append((handle,kind,size,ctypes.string_at(address,len(after))))
                    return mode!='set_fail'
                token._metadata=metadata;token.a=SimpleNamespace(SetTokenInformation=setter)
                if mode in ('ok','already_present'):
                    receipt=token._ensure_user_default_acl(user)
                    self.assertTrue(receipt['old_aces_preserved']);self.assertTrue(receipt['only_derived_token_default_modified'])
                    self.assertFalse(receipt['OS_file_or_desktop_ACLs_modified'])
                else:
                    with self.assertRaises(b.Stop):token._ensure_user_default_acl(user)
                self.assertEqual(calls,[] if mode=='already_present' else [('derived',6,ctypes.sizeof(ctypes.c_void_p),after)])
        tree=ast.parse(Path(r.__file__).read_text(encoding='utf-8'))
        opens=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='OpenProcessToken']
        self.assertEqual(sorted(n.args[1].value for n in opens),[8,0x8B])

    def test_case_entrypoint_coverage(self):
        text=Path(cases.__file__).read_text(encoding='utf-8')
        for marker in ('cross_execute:','secret_select','factor_update','epoch_update','authority_update',
            'cannot_set_role:','forged_context:','dynamic_sql','pool_context_switch_b','pinned_definer_search_path',
            'composite_executor_fk','status_owner_positive_lock','status_lock_privilege_not_dml',
            'table_owner_force_rls','full_install_late_failure_atomic_rollback','executor_revocation','target_epoch_independent'):
            with self.subTest(marker=marker):self.assertIn(marker,text)

def run():
    sys.addaudithook(audit)
    log=io.StringIO()
    result=unittest.TextTestRunner(stream=log,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Checks))
    report={'passed':result.wasSuccessful() and not result.skipped,'tests':result.testsRun,'failures':len(result.failures),
        'errors':len(result.errors),'skipped':len(result.skipped),'evidence_level':'synthetic_only','database_connections':0,
        'postgres_processes_started':0,'database_execution_allowed':False,'log':log.getvalue()}
    return report

if __name__=='__main__':
    report=run()
    # Exact guest home only; host caller captures stdout into its task output.
    if Path(__file__).resolve().parent==b.HOME:b.write(b.HOME/'SELFTEST.json',report)
    print(json.dumps(report,ensure_ascii=True))
    raise SystemExit(0 if report['passed'] else 1)
