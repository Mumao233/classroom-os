"""Host/guest synthetic checks. Never open PG/network or create processes."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import ast,hashlib,json,io,re,struct,unittest
from datetime import datetime,timedelta,timezone
import common as b,crypto,protocol,schema
class Checks(unittest.TestCase):
    def test_pool_close_attempts_every_connection_and_retains_first_error(self):
        import service_pool,request_diagnostics as rd
        pool=service_pool.FixedPool(object(),('auth','session','runtime'))
        calls=[];first=ConnectionError('PRIVATE_FIRST_CLOSE');second=RuntimeError('PRIVATE_SECOND_CLOSE')
        def close(key):
            self.assertTrue(all(q.empty() for q in pool.channels.values()))
            calls.append(key)
            if key==('auth',0):raise first
            if key==('session',0):raise second
        for key,stack in pool.stacks.items():stack.callback(close,key)
        recorder=rd.Recorder()
        with recorder.request('GET','/lab/business'):
            with self.assertRaises(ConnectionError) as caught:pool.close_pool()
        self.assertIs(caught.exception,first)
        self.assertEqual(calls,list(pool.stacks))
        self.assertTrue(pool.closing)
        self.assertEqual({k:q.qsize() for k,q in pool.channels.items()},{k:pool.pool_size(k) for k in pool.channels})
        row=recorder.snapshot()['requests'][0]
        self.assertEqual(row['first_exception']['stage'],'pool.close.auth')
        self.assertTrue(any(e.get('error_type')=='RuntimeError' and e['secondary'] for e in row['last_events']))
        self.assertNotIn('PRIVATE',json.dumps(row))
        pool.close_pool();self.assertEqual(calls,list(pool.stacks))
    def test_pool_close_uses_one_drain_budget_and_returns_partial_claims(self):
        import service_pool,admission,queue,types
        from unittest.mock import patch
        pool=service_pool.FixedPool(object(),('auth','session','runtime'))
        clock=[0.0];waits=[];claims=[];returns=[];closed=[]
        class Channel:
            def __init__(self,service):self.service=service;self.next=0
            def get(self,timeout):
                waits.append(timeout)
                if timeout<.4:
                    clock[0]+=timeout;raise queue.Empty
                clock[0]+=.4;index=self.next;self.next+=1
                claims.append((self.service,index));return index
            def put(self,index):returns.append((self.service,index))
        pool.channels={k:Channel(k) for k in pool.channels}
        for key,stack in pool.stacks.items():stack.callback(closed.append,key)
        with patch.object(service_pool,'time',types.SimpleNamespace(monotonic=lambda:clock[0])):
            with self.assertRaisesRegex(admission.Expired,'pool_close_deadline'):pool.close_pool()
        self.assertAlmostEqual(clock[0],5)
        self.assertTrue(all(0<v<=5 for v in waits));self.assertEqual(waits,sorted(waits,reverse=True))
        self.assertEqual(returns,claims);self.assertEqual(closed,[]);self.assertTrue(pool.closing)
        # Failed draining neither closes a busy connection nor consumes its
        # cleanup callback: an owner may retry after in-flight work exits.
        from contextlib import ExitStack
        with ExitStack() as remaining:
            for stack in pool.stacks.values():remaining.callback(stack.close)
        self.assertEqual(len(closed),len(pool.stacks))
    def test_pool_close_does_not_inherit_expired_request_deadline(self):
        import service_pool,admission,time
        pool=service_pool.FixedPool(object());closed=[]
        for key,stack in pool.stacks.items():stack.callback(closed.append,key)
        token=admission.CURRENT.set(time.monotonic()-1)
        try:pool.close_pool()
        finally:admission.CURRENT.reset(token)
        self.assertEqual(closed,list(pool.stacks))
        with self.assertRaises(b.Stop):pool.transaction('auth',lambda c:None)
    def test_snapshot_timeout_preserves_original_20s_budget_and_reaps_owned_child(self):
        import types,subprocess,time
        tree=ast.parse((Path(__file__).parent/'runner.py').read_bytes())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_snapshot')
        calls=[]
        class Child:
            pid=123;returncode=None
            def __enter__(self):return self
            def __exit__(self,*args):calls.append('closed')
            def communicate(self,timeout=None):
                calls.append(('communicate',timeout))
                if timeout is not None:raise subprocess.TimeoutExpired('fixed',timeout)
                return b'bounded_metadata',b''
            def kill(self):self.returncode=1;calls.append('kill')
        proxy=types.SimpleNamespace(Popen=lambda *a,**k:Child(),PIPE=subprocess.PIPE,TimeoutExpired=subprocess.TimeoutExpired,CompletedProcess=subprocess.CompletedProcess)
        ns={'subprocess':proxy,'time':time,'b':b};exec(compile(ast.Module(body=[fn],type_ignores=[]),'isolated_snapshot_timeout','exec'),ns)
        rows=[]
        with self.assertRaises(subprocess.TimeoutExpired):ns['run_snapshot'](['fixed'],'ordinary',rows)
        self.assertEqual(calls,[('communicate',20),'kill',('communicate',None),'closed'])
        self.assertEqual(rows[0]['budget_seconds'],20);self.assertTrue(rows[0]['owned_child_killed_and_reaped'])
        self.assertNotIn('bounded_metadata',json.dumps(rows))
    def test_native_file_batch_keeps_full_hash_metadata_names_and_deadlines(self):
        import native_files,source_pin,guard_io,ctypes as c,os
        from ctypes import wintypes as w
        path=Path(__file__).parent/'native_identity.c';raw=path.read_bytes()
        kernel=c.WinDLL('kernel32',use_last_error=True)
        kernel.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE];kernel.CreateFileW.restype=w.HANDLE
        kernel.GetFileInformationByHandle.argtypes=[w.HANDLE,c.POINTER(source_pin.Info)];kernel.GetFileInformationByHandle.restype=w.BOOL
        kernel.CloseHandle.argtypes=[w.HANDLE];kernel.CloseHandle.restype=w.BOOL
        handle=kernel.CreateFileW(str(path),0x80000000,1,None,3,0,None);self.assertTrue(handle)
        value=source_pin.Info();self.assertTrue(kernel.GetFileInformationByHandle(handle,c.byref(value)));identity=source_pin.facts(value,False)
        library=native_files.Library();scratch=c.create_unicode_buffer(32768);opened=True;entries=[]
        def make(data=None,name=None):
            item=native_files.Entries([(handle,identity,name,data,hashlib.sha256(raw).hexdigest() if data is not None else None)]);entries.append(item);return item
        try:
            # Keep guard_io's cached CPython function object live before and
            # after Entries construction, as happens in the real runtime.
            with path.open('rb') as stream:
                mapped=guard_io.ReadOnlyView(stream.fileno())
                try:
                    self.assertTrue(mapped==raw)
                    scratch_entry=make(raw)
                    library.check(scratch_entry,scratch,hashes=True)
                    self.assertTrue(mapped==raw);self.assertTrue(mapped==mapped)
                finally:mapped.close()
            original=make(raw);library.check(original,scratch,hashes=True)
            named=make(name=path);library.check(named,scratch);named.array[0].path_after=1;library.check(named,scratch)
            for field in ('volume','index_hi','index_lo','attributes','size_hi','size_lo','links','written_hi','written_lo'):
                value=getattr(original.array[0],field);setattr(original.array[0],field,value^1)
                with self.assertRaises(b.Stop):library.check(original,scratch,hashes=True)
                setattr(original.array[0],field,value)
            for changed in (bytes([raw[0]^1])+raw[1:],raw[:-1]+bytes([raw[-1]^1])):
                with self.assertRaises(b.Stop):library.check(make(changed),scratch,hashes=True)
            with self.assertRaises(b.Stop):library.check(make(name=path.with_name('wrong-name.c')),scratch)
            token=native_files.admission.CURRENT.set(native_files.time.monotonic()-1)
            try:
                with self.assertRaises(native_files.admission.Expired):library.check(original,scratch,hashes=True)
            finally:native_files.admission.CURRENT.reset(token)
            self.assertTrue(kernel.CloseHandle(handle));opened=False
            with self.assertRaises(b.Stop):library.check(original,scratch,hashes=True)
        finally:
            if opened:kernel.CloseHandle(handle)
            library.close()
            for item in entries:item.close()
        with self.assertRaises(b.Stop):library.check(original,scratch)
    def test_native_identity_matches_original_and_rejects_bad_inputs_and_closed_use(self):
        import native_identity,os,ctypes as c
        from ctypes import wintypes as w
        source=b.ROOT/'tools/pr03b/target_guard.py'
        if not source.is_file():source=Path(__file__).resolve().parents[1]/'target_guard.py'
        # Execute the frozen observer and its pure syntax parser verbatim.
        # Importing the entire guard would require SQLAlchemy before the
        # isolated guest dependency bootstrap, although this check uses no PG.
        import types,dataclasses
        tree=ast.parse(source.read_bytes())
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('_absolute','observe_windows_process') or isinstance(n,ast.ClassDef) and n.name=='ProcessIdentity']
        self.assertEqual(len(nodes),3)
        def reject(reason):raise b.Stop(reason)
        ns={'os':os,'ctypes':c,'Path':Path,'re':re,'_CONTROL':re.compile(r'[\x00-\x1f\x7f\ud800-\udfff]'),'_reject':reject,'dataclass':dataclasses.dataclass}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),ns)
        original=types.SimpleNamespace(**ns)
        bridge=native_identity.Library();kernel=c.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
        kernel.CloseHandle.argtypes=[w.HANDLE];kernel.CloseHandle.restype=w.BOOL
        handle=kernel.OpenProcess(0x1000,False,os.getpid());self.assertTrue(handle)
        try:
            for _ in range(20):
                expected=original.observe_windows_process(os.getpid())
                for actual in (bridge.pid(os.getpid()),bridge.handle(handle,os.getpid())):
                    self.assertEqual(actual,(expected.creation_token,str(expected.executable)))
            for value in (False,0,-1,0x100000000,[],None):
                with self.assertRaises(native_identity.Rejected):bridge.pid(value)
            with self.assertRaises(native_identity.Rejected):bridge.handle(None,os.getpid())
            with self.assertRaises(native_identity.Rejected):bridge.handle(handle,os.getpid()+1)
            token=c.c_ulonglong();buf=c.create_unicode_buffer(2);length=w.DWORD();error=w.DWORD()
            for capacity in (0,1,32769):
                self.assertFalse(bridge.pid_function(os.getpid(),c.byref(token),buf,capacity,c.byref(length),c.byref(error)))
                self.assertEqual(error.value,87)
        finally:kernel.CloseHandle(handle);bridge.close()
        with self.assertRaisesRegex(b.Stop,'closed'):bridge.pid(os.getpid())
    def test_cached_syntax_matches_frozen_original_for_valid_and_hostile_inputs(self):
        import native_bindings,types,os
        source=b.ROOT/'tools/pr03b/target_guard.py'
        if not source.is_file():source=Path(__file__).resolve().parents[1]/'target_guard.py'
        tree=ast.parse(source.read_bytes());function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_absolute')
        def reject(reason):raise b.Stop(reason)
        namespace={'Path':Path,'os':os,'re':re,'_CONTROL':re.compile(r'[\x00-\x1f\x7f\ud800-\udfff]'),'_reject':reject}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'original_absolute_syntax','exec'),namespace)
        original=namespace['_absolute'];cache=native_bindings.SyntaxPaths(types.SimpleNamespace(_absolute=original,Path=Path))
        values=[r'C:\PR03bSyntaxOnly\fixed',r'C:/PR03bSyntaxOnly/fixed',r'\\server\share',r'\\?\C:\fixed',r'C:\a\..\b',r'C:\a:stream',r'C:\trailing.\file','relative','',None,[],{},False,17,'C:\\a\x00bad','C:\\a\ud800bad']
        def outcome(call,value):
            try:return ('accepted',str(call(value)))
            except b.Stop as ex:return ('rejected',str(ex))
        for value in values:
            for _ in range(2):self.assertEqual(outcome(cache,value),outcome(original,value))
    def test_pure_syntax_cache_never_caches_metadata_or_bad_type_rejection(self):
        import native_bindings,types
        parsed=[]
        def parse(value):
            parsed.append(value)
            if type(value) is not str or not value.startswith('fixed:'):raise b.Stop('synthetic_invalid_syntax')
            return Path(value[6:])
        guard=types.SimpleNamespace(_absolute=parse,Path=Path);cache=native_bindings.SyntaxPaths(guard)
        a=cache('fixed:a');self.assertIs(a,cache('fixed:a'));self.assertEqual(parsed,['fixed:a'])
        for value in ([],[], 'bad','bad'):
            with self.assertRaisesRegex(b.Stop,'invalid_syntax'):cache(value)
        self.assertEqual(len(parsed),5)
        for i in range(300):cache('fixed:'+str(i))
        self.assertEqual(cache.cached.cache_info().currsize,256)
        guard.Path=object()
        with self.assertRaisesRegex(b.Stop,'context_changed'):cache('fixed:a')
    def test_worker_window_requires_all_pools_ready_and_preserves_offset(self):
        import performance,threading,time
        for offset in (1000,1015):
            schedule=performance.WorkerSchedule(2,offset);values=[]
            with self.assertRaisesRegex(b.Stop,'not_ready'):schedule.arm(time.monotonic()+1)
            threads=[threading.Thread(target=lambda:values.append(schedule.warm_and_start())) for _ in range(2)]
            for thread in threads:thread.start()
            self.assertTrue(schedule.ready.wait(2));self.assertEqual(values,[])
            self.assertFalse(schedule.evidence()['ready_before_arm'])
            start=time.monotonic()+1;schedule.arm(start)
            for thread in threads:thread.join(2);self.assertFalse(thread.is_alive())
            self.assertEqual(values,[start+offset]*2);self.assertTrue(schedule.evidence()['ready_before_arm'])
            with self.assertRaisesRegex(b.Stop,'already_armed'):schedule.arm(start)
    def test_worker_window_cancellation_unblocks_prearmed_wait_without_launch(self):
        import performance,threading
        schedule=performance.WorkerSchedule(1,1000);result=[]
        def wait():
            try:schedule.warm_and_start()
            except b.Stop as ex:result.append(str(ex))
        thread=threading.Thread(target=wait);thread.start();self.assertTrue(schedule.ready.wait(2))
        schedule.cancel();thread.join(2);self.assertFalse(thread.is_alive())
        self.assertEqual(result,['worker_window_cancelled']);self.assertIsNone(schedule.evidence()['start_at'])
    def test_guard_DLL_bindings_cached_per_thread_but_queries_are_not(self):
        import native_bindings,types,threading
        made=[];observed=[]
        def factory(*args,**kw):
            made.append(args);return types.SimpleNamespace(observe=lambda:observed.append(len(observed)+1) or observed[-1])
        original=types.SimpleNamespace(WinDLL=factory,PyDLL=factory,unrelated=object());proxy=native_bindings.GuardLibraries(original)
        a=proxy.WinDLL('kernel32',use_last_error=True);z=proxy.WinDLL('kernel32',use_last_error=True)
        self.assertIs(a,z);self.assertEqual([a.observe(),z.observe()],[1,2]);self.assertEqual(len(made),1)
        self.assertEqual(made[0],(r'C:\Windows\System32\kernel32.dll',))
        proxy.WinDLL('other.dll',use_last_error=True);self.assertEqual(len(made),2)
        self.assertIs(proxy.unrelated,original.unrelated)
        def run():proxy.WinDLL('kernel32',use_last_error=True)
        thread=threading.Thread(target=run);thread.start();thread.join(2);self.assertFalse(thread.is_alive());self.assertEqual(len(made),3)
    def test_source_expected_name_cache_never_caches_actual_metadata(self):
        import source_pin,types
        bundle=source_pin.Bundle.__new__(source_pin.Bundle);bundle.opened_names=True;bundle.expected_names={}
        path=Path('fixed-synthetic-source');actual={'name':'\\\\?\\'+str(path),'calls':0}
        def query(h,buf,size,flags):actual['calls']+=1;buf.value=actual['name'];return len(buf.value)
        bundle.k=types.SimpleNamespace(GetFinalPathNameByHandleW=query)
        bundle.named(1,path);bundle.named(1,path)
        self.assertEqual(actual['calls'],2)
        actual['name']='\\\\?\\wrong-current-path'
        with self.assertRaisesRegex(b.Stop,'named_path_changed'):bundle.named(1,path)
        self.assertEqual(actual['calls'],3)
    def test_owned_worker_cancel_before_and_after_registration_is_idempotent(self):
        import performance
        for early in (True,False):
            cancelled=[];control=performance.OwnedCancellation()
            if early:control.cancel()
            control.register(lambda:cancelled.append('only_this_run_owned_job'))
            control.cancel();control.cancel()
            self.assertEqual(cancelled,['only_this_run_owned_job'])
            with self.assertRaisesRegex(b.Stop,'registered_twice'):control.register(lambda:None)
    def test_short_metadata_calls_are_fresh_and_waits_keep_original_binding(self):
        import native_bindings,types
        from unittest.mock import patch
        calls=[]
        class Query:
            argtypes=['fixed'];restype='result'
            def __call__(self,*args):calls.append(args);return len(calls)
        original=types.SimpleNamespace(Query=Query(),WaitForSingleObject=object(),CreateProcessW=object())
        fresh=Query();library=types.SimpleNamespace(Query=fresh)
        with patch.object(native_bindings.ctypes,'PyDLL',return_value=library) as load:
            adapter=native_bindings.ShortCalls(original,'kernel32.dll',('Query',))
        load.assert_called_once_with('C:\\Windows\\System32\\kernel32.dll',use_last_error=True)
        self.assertEqual([adapter.Query(1),adapter.Query(2)],[1,2])
        self.assertEqual(fresh.argtypes,original.Query.argtypes)
        self.assertIs(adapter.WaitForSingleObject,original.WaitForSingleObject)
        self.assertIs(adapter.CreateProcessW,original.CreateProcessW)
        self.assertEqual(calls,[(1,),(2,)])
    def test_overload_coverage_uses_actual_hash_start_after_queue(self):
        import capacity
        rows=[{'expensive_calls':1,'started_monotonic':1009.8,'queue_ms':500},
              {'expensive_calls':0,'started_monotonic':1001,'queue_ms':None},
              {'expensive_calls':1,'started_monotonic':1019.8,'queue_ms':500}]
        self.assertEqual(capacity.actual_hash_windows(rows,1000,20),[0,1])
    def test_cached_native_bindings_never_share_Job_wrappers_or_observations(self):
        import native_bindings,types,threading
        from concurrent.futures import ThreadPoolExecutor
        created=[];observed=[]
        class Library:
            def observe(self):observed.append(len(observed)+1);return observed[-1]
        class Original:
            def __init__(self):created.append(True);self.k=Library();self.ip=object();self.nt=object();self.sh=object()
        native=types.SimpleNamespace(_Api=Original);guard=types.SimpleNamespace(ctypes=object(),_absolute=Path,Path=Path);guard_ctypes=guard.ctypes;absolute=guard._absolute;cache=native_bindings.Cache(native,guard)
        try:
            a=native._Api();z=native._Api();self.assertIsNot(a,z);self.assertIs(a.k,z.k)
            raw=z.k;a.k=types.SimpleNamespace(CreateProcessW='private_restricted_or_resource_proxy')
            other=native._Api();self.assertIs(other.k,raw);self.assertNotIn('CreateProcessW',vars(other.k))
            self.assertEqual([z.k.observe(),other.k.observe()],[1,2])
            barrier=threading.Barrier(2)
            def run(_):barrier.wait(2);first=native._Api();second=native._Api();return first is not second and first.k is second.k
            with ThreadPoolExecutor(max_workers=2) as workers:self.assertTrue(all(workers.map(run,range(2))))
            self.assertEqual(len(created),3);self.assertEqual(cache.statistics()['API_objects_created'],7)
        finally:cache.close()
        self.assertIs(native._Api,Original)
        self.assertIs(guard.ctypes,guard_ctypes)
        self.assertIs(guard._absolute,absolute)
    def test_source_mapping_is_rehashed_and_writable_view_is_rejected(self):
        import source_pin,threading,queue
        bundle=source_pin.Bundle.__new__(source_pin.Bundle)
        bundle.lock=threading.RLock();bundle.cv=threading.Condition(bundle.lock)
        bundle.closed=False;bundle.closing=False;bundle.active=0;bundle.borrowed=0;bundle.peak_borrowed=0;bundle.completed=0
        bundle.home=Path('synthetic-no-file-io');bundle.digest='synthetic';bundle.directories=[]
        bundle.names_locked=True
        identity=(1,2,3);payload=b'current-source';bundle.files={'fixture':(123,identity,hashlib.sha256(payload).hexdigest())}
        reader={'files':{'fixture':71},'views':{'fixture':memoryview(payload)},'data':None,'name':None}
        bundle.readers=queue.Queue();bundle.readers.put(reader);bundle.named=lambda *args:self.fail('locked file name needs no new query');bundle.info=lambda *args:identity
        bundle.content=lambda *args:self.fail('mapped inputs must not substitute an old byte snapshot')
        self.assertEqual(bundle.verify(),'synthetic')
        reader['views']['fixture']=memoryview(b'changed-source')
        with self.assertRaisesRegex(b.Stop,'pinned_source_content_changed'):bundle.verify()
        reader['views']['fixture']=memoryview(bytearray(payload))
        with self.assertRaisesRegex(b.Stop,'source_mapping_not_readonly'):bundle.verify()
        reader['views']['fixture']=memoryview(payload);bundle.info=lambda *args:('changed-current-identity',)
        with self.assertRaisesRegex(b.Stop,'source_file_identity_changed'):bundle.verify()
        self.assertEqual(bundle.completed,1);self.assertEqual(bundle.active,0);self.assertEqual(bundle.borrowed,0)
    def test_capacity_HTTP200_without_authenticated_state_is_not_login_success(self):
        import capacity
        base={'kind':'login','status':200,'ms':100,'timeout':False,'dispatch_lag_ms':0}
        rows=[dict(base,authenticated=False),dict(base,authenticated=True),dict(base,status=429,authenticated=False)]
        value=capacity.stats(rows,'login')
        self.assertEqual(value['offered'],3);self.assertEqual(value['successes'],1);self.assertEqual(value['rejects'],1)
    def test_role_pools_are_finite_distinct_and_keep_all_native_boundaries(self):
        import service_pool
        pool=service_pool.FixedPool(object(),('auth','session','runtime'))
        try:
            self.assertEqual({name:q.qsize() for name,q in pool.channels.items()},{'auth':2,'session':8,'runtime':8})
            self.assertEqual(len(pool.stacks),18)
            self.assertEqual(len({id(q) for q in pool.channels.values()}),3)
            self.assertEqual(pool.pool,{})
            with self.assertRaises(b.Stop):pool.pool_size('bootstrap')
        finally:pool.close_pool()
    def test_readonly_guard_view_hashes_and_compares_full_buffers(self):
        import guard_io
        with Path(__file__).open('rb') as source:
            raw=source.read()
            with guard_io.ReadOnlyView(source.fileno()) as a,guard_io.ReadOnlyView(source.fileno()) as z:
                self.assertEqual(hashlib.sha256(a).digest(),hashlib.sha256(raw).digest())
                self.assertTrue(a==z);self.assertFalse(a!=a);self.assertTrue(a==raw)
                self.assertTrue(a!=bytes([raw[0]^1])+raw[1:])
                self.assertTrue(a!=raw[:-1]+bytes([raw[-1]^1]))
                self.assertTrue(a!=raw[:-1])
                with self.assertRaises(TypeError):a[0]=0
                borrowed=memoryview(a)
                try:
                    with self.assertRaises(BufferError):a.close()
                finally:borrowed.release()
    def test_guard_immutable_reader_rechecks_bytes_metadata_and_scope(self):
        import guard_io,ctypes as c,threading,queue,types,admission,time
        class Views:
            def __getitem__(self,key):reads.append(True);return data[0]
        reads=[];data=[b'old'];path=Path('synthetic-immutable');identity=tuple(range(9));events=[]
        obj=guard_io.Reads.__new__(guard_io.Reads);obj.original=lambda p,n:('original',p,n)
        obj.lock=threading.RLock();obj.cv=threading.Condition(obj.lock);obj.closed=False;obj.closing=False;obj.active=0;obj.calls=0;obj.bytes=0
        obj.files={path:(identity,3,b.sha(b'old'))};obj.ancestors={path:[(1,('dir',),Path('synthetic-ancestor'))]}
        obj.readers=queue.Queue();obj.readers.put({'members':{path:2},'views':Views(),'name':None})
        obj.named=lambda *args:events.append('name');obj.info=lambda h,d:('dir',) if d else identity
        self.assertEqual(obj.read(path,3),b'old');data[0]=b'new';self.assertEqual(obj.read(path,3),b'new')
        self.assertNotEqual(b.sha(obj.read(path,3)),obj.files[path][2]);self.assertEqual(len(reads),3)
        self.assertEqual(obj.read(Path('owner.json'),32768),('original',Path('owner.json'),32768))
        with self.assertRaises(b.Stop):obj.read(path,2)
        obj.info=lambda h,d:('dir',) if d else ('different',)
        with self.assertRaises(b.Stop):obj.read(path,3)
        self.assertEqual(obj.active,0);self.assertEqual(obj.readers.qsize(),1)
        token=admission.CURRENT.set(time.monotonic()-1)
        try:
            with self.assertRaises(admission.Expired):obj.read(path,3)
        finally:admission.CURRENT.reset(token)
        self.assertEqual(len(reads),3)
    def test_request_correlation_is_bounded_and_never_authorizes(self):
        import request_diagnostics as rd
        recorder=rd.Recorder()
        with recorder.request('POST','/lab/login','a'*32):
            rd.failure(RuntimeError('PRIVATE_SENTINEL'),'test.original')
        with recorder.request('GET','/lab/business','PRIVATE_SENTINEL'):
            pass
        rows=recorder.snapshot()['requests']
        self.assertEqual(rows[0]['client_request_id'],'a'*32)
        self.assertIsNone(rows[1]['client_request_id'])
        self.assertNotEqual(rows[0]['request_id'],rows[0]['client_request_id'])
        self.assertNotIn('PRIVATE_SENTINEL',json.dumps(recorder.snapshot()))
    def test_expired_BEGIN_still_rolls_back_before_pool_reuse(self):
        import admission,service_pool,threading,time,types
        from unittest.mock import patch
        from contextlib import contextmanager,ExitStack
        calls=[]
        class Connection:
            def execute(self,sql,args=()):
                calls.append(sql)
                if sql=='BEGIN READ WRITE':
                    admission.CURRENT.set(time.monotonic()-1)
                    raise admission.Expired('injected_boundary_expiry')
                if sql=='ROLLBACK':self_test.assertGreater(admission.remaining(),0)
        @contextmanager
        def connect(service):yield Connection()
        self_test=self
        lab=service_pool.FixedPool.__new__(service_pool.FixedPool)
        lab.lease=types.SimpleNamespace(connect=connect);lab.pool_stack=ExitStack();lab.pool={};lab.pool_locks={'auth':threading.RLock()};lab.closing=False
        token=admission.CURRENT.set(time.monotonic()+1)
        try:
            with patch.dict(sys.modules,{'native_process':types.SimpleNamespace()}),self.assertRaises(admission.Expired):lab.database('auth','SELECT must_not_execute')
            self.assertEqual(calls,['BEGIN READ WRITE','ROLLBACK'])
        finally:admission.CURRENT.reset(token);lab.pool_stack.close()
    def test_independent_Labs_do_not_reuse_session_ids(self):
        import transport,threading
        ids=[]
        for _ in range(2):
            lab=transport.Lab.__new__(transport.Lab)
            lab.lock=threading.Lock();lab.markers=set();lab.sessions={};lab.next_id=700000
            lab.sql=lambda *a:{'owner':2,'epoch':1,'role':'student'}
            token,row=lab.login(2,password_version=1);ids.append(row['id'])
        self.assertEqual(len(set(ids)),2)
    def test_admission_and_operation_deadlines_are_distinct(self):
        import admission,time
        gate=admission.Bounded()
        def work():admission.granted();time.sleep(.05);return 42
        try:self.assertEqual(gate.call(work,deadline=time.monotonic()+.02),42)
        finally:gate.close()
    def test_observations_overlap_but_backend_churn_is_exclusive(self):
        import observation_gate,threading
        gate=observation_gate.Gate();both=threading.Barrier(3);release=threading.Event();writer=threading.Event()
        def observe():
            with gate.shared():both.wait(2);release.wait(2)
        threads=[threading.Thread(target=observe) for _ in range(2)]
        for t in threads:t.start()
        both.wait(2)
        def change():
            with gate:
                with gate.shared(),gate:writer.set()
        t=threading.Thread(target=change);t.start()
        self.assertFalse(writer.wait(.02));release.set()
        for r in threads:r.join(2)
        t.join(2);self.assertTrue(writer.is_set());self.assertFalse(t.is_alive())
    def test_revoked_D0_is_not_a_current_authorization(self):
        import types
        from unittest.mock import patch
        d=protocol.D0.__new__(protocol.D0)
        d.pin=types.SimpleNamespace(verify=lambda *a:None)
        d.published={'deployment':'synthetic','version':2,'enabled':False}
        pg=(2,'0'*64)
        body={'nonce':'fresh','phase':'ready','version':2,'hash':pg[1],'enabled':False}
        with patch.object(crypto,'signed',return_value=b''),self.assertRaisesRegex(b.Stop,'D0_revoked'):d.ready(pg,nonce='fresh',response=(body,b''))
        body['enabled']=True
        with patch.object(crypto,'signed',return_value=b''),self.assertRaisesRegex(b.Stop,'D0_revoked'):d.ready(pg,nonce='fresh',response=(body,b''))
    def test_timeout_does_not_release_executing_slots(self):
        import admission,threading,time
        gate=admission.Bounded();finish=threading.Event();entered=threading.Event()
        def work():entered.set();finish.wait(30)
        try:
            for _ in range(10):
                with self.assertRaises(admission.Expired):gate.call(work,deadline=time.monotonic()+.2)
            self.assertEqual(gate.outstanding,10)
            before=time.monotonic()
            with self.assertRaisesRegex(admission.Expired,'ingress_full'):gate.call(lambda:None)
            self.assertLess(time.monotonic()-before,.1)
        finally:finish.set();gate.close()
        self.assertEqual(gate.outstanding,0)
    def test_pool_lock_uses_the_ingress_deadline(self):
        import admission,threading,time
        lock=threading.Lock();lock.acquire();executed=[]
        token=admission.CURRENT.set(time.monotonic()+.01)
        try:
            with self.assertRaises(admission.Expired):
                with admission.locked(lock):executed.append(True)
        finally:admission.CURRENT.reset(token);lock.release()
        self.assertEqual(executed,[])
    def test_source_pin_wait_is_inside_same_ingress_deadline(self):
        import source_pin,admission,threading,time
        bundle=source_pin.Bundle.__new__(source_pin.Bundle);bundle.lock=threading.Lock();bundle.lock.acquire()
        token=admission.CURRENT.set(time.monotonic()+.02);began=time.monotonic()
        try:
            with self.assertRaises(admission.Expired):bundle.verify()
            self.assertLess(time.monotonic()-began,.2)
        finally:admission.CURRENT.reset(token);bundle.lock.release()
    def test_revocation_and_MFA_lock_checks_are_atomic(self):
        sql=schema.extension('a'*32)
        reg=sql[sql.index('CREATE FUNCTION identity_api.lab3_registration'):]
        self.assertIn('SET os_key_current=en WHERE actor=5',reg)
        self.assertIn('SET enabled=en WHERE actor=5',reg)
        mfa=sql[sql.index('CREATE FUNCTION identity_api.lab3_mfa_step'):sql.index('CREATE FUNCTION identity_api.lab3_step')]
        self.assertLess(mfa.index('FOR UPDATE'),mfa.index('n:=clock_timestamp()'))
        self.assertLess(mfa.index('n>=until_at'),mfa.index('SET last_step=st'))
    def test_asset_manifest_is_the_only_unlisted_member(self):
        self.assertTrue(b.dependency_members({'library.pyd','ASSETS-MANIFEST.json'},{'library.pyd'}))
        for seen in ({'library.pyd'},{'library.pyd','ASSETS-MANIFEST.json','extra.py'},{'ASSETS-MANIFEST.json'}):self.assertFalse(b.dependency_members(seen,{'library.pyd'}))
    def test_password_scalars_and_roles(self):
        for role,low in (('student',6),('admin',12)):
            for n in (low,1024):self.assertEqual(crypto.password('😀'*n,role),'😀'*n)
            for v in ('x'*(low-1),'x'*1025,'\ud800'*low,None):
                with self.subTest(role=role,value_type=type(v).__name__):
                    with self.assertRaises(b.Stop):crypto.password(v,role)
        self.assertNotEqual(crypto.typed('e\u0301'),crypto.typed('é'))
    def test_typed_binding_not_json_or_normalization(self):
        values=[None,False,0,'0',[],{},[1],{'x':1},{'x':'1'},{'x':True}]
        self.assertEqual(len({crypto.typed(v) for v in values}),len(values))
        self.assertEqual(crypto.typed({'b':1,'a':2}),crypto.typed({'a':2,'b':1}))
        for v in (1.0,{1:'bad'},object()):
            with self.assertRaises(b.Stop):crypto.typed(v)
    def test_canonical_base64(self):
        raw=bytes(range(32));value=crypto.b64(raw);self.assertEqual(crypto.unb64(value,32),raw)
        for v in (value+'=',value[:-1],value+'x','!'+value):
            with self.assertRaises(b.Stop):crypto.unb64(v,32)
    def test_phc_cheap_bounds(self):
        for value in ('$argon2id$v=19$m=65536,t=3,p=1$YWJj$YWJj','$argon2id$v=19$m=19456,t=2,p=1$YWJj$YWJj'):
            self.assertTrue(crypto.phc_bounded(value))
        for value in ('x'*513,'$argon2i$v=19$m=65536,t=3,p=1$a$a','$argon2id$v=16$m=65536,t=3,p=1$a$a','$argon2id$v=19$m=9999999999999999,t=3,p=1$a$a','$argon2id$v=19$m=65536,t=5,p=1$a$a','$argon2id$v=19$m=65536,t=3,p=5$a$a'):
            with self.assertRaises(b.Stop):crypto.phc_bounded(value)
    def test_two_time_predicates(self):
        now=datetime.now(timezone.utc);challenge=now+timedelta(seconds=300);qualification=now+timedelta(seconds=900)
        self.assertEqual(protocol.proof_times(now+timedelta(seconds=299),challenge,qualification),now+timedelta(seconds=419))
        for v in (challenge,challenge+timedelta(microseconds=1)):
            with self.assertRaises(b.Stop):protocol.proof_times(v,challenge,qualification)
        for qual,pending in ((now,None),(qualification,now)):
            with self.assertRaises(b.Stop):protocol.proof_times(now,challenge,qual,pending)
        self.assertEqual(protocol.proof_times(now,challenge,now+timedelta(seconds=10)),now+timedelta(seconds=10))
    def test_SQL_preserves_base_and_only_lab_objects(self):
        sql=schema.extension('a'*32)
        self.assertNotIn('CREATE EXTENSION',sql);self.assertNotIn('GRANT ALL',sql)
        adoption=r'\bCREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX|SCHEMA|SEQUENCE)\s+IF\s+NOT\s+EXISTS\b'
        self.assertIsNone(re.search(adoption,sql,re.I))
        self.assertIsNotNone(re.search(adoption,'CREATE TABLE IF NOT EXISTS identity_control.lab3_bad(id int)',re.I))
        self.assertIsNone(re.search(adoption,'IF NOT EXISTS(SELECT 1) THEN RAISE EXCEPTION',re.I))
        for table in schema.TABLES:self.assertIn('lab3_'+table+' FORCE ROW LEVEL SECURITY',sql)
        self.assertIn("clock_timestamp()",sql);self.assertIn("WITH CHECK(false)",sql)
        self.assertNotIn('business_counter',sql[sql.index('CREATE FUNCTION identity_api.lab3_write'):])
        for name in ('status','write','step','notice'):self.assertIn('REVOKE ALL ON FUNCTION identity_api.lab3_'+name,sql)
    def test_approved_base_is_not_edited(self):
        self.assertEqual(schema.install('a'*32),schema.base.install('a'*32))
    def test_sources_parse(self):
        for p in Path(__file__).parent.glob('*.py'):ast.parse(p.read_text(encoding='utf-8'))
    def test_retained_backend_handle_exit_is_not_membership(self):
        import types
        from unittest.mock import patch
        import backend_lifecycle
        for wait,code,active,rows,allowed in ((0,0,False,[],True),(258,259,True,[],False),(0,1,False,[],False),(0,0,True,[],False),(0,0,False,[{}],False)):
            closed=[];events=[];waits=[];now=[0.0]
            def sleep(seconds):now[0]+=seconds
            class Kernel:
                def WaitForSingleObject(self,h,ms):waits.append(ms);return wait
                def GetExitCodeProcess(self,h,ptr):ptr._obj.value=code;return True
                def CloseHandle(self,h):closed.append(h);return True
            job=types.SimpleNamespace(api=types.SimpleNamespace(k=Kernel()),contains=lambda pid:True,process_ids=lambda:(42,) if active else ())
            lease=types.SimpleNamespace(job=job,events=events)
            with patch.dict(sys.modules,{'native_process':types.SimpleNamespace(tcp_rows=lambda p:rows)}),patch.object(backend_lifecycle,'time',types.SimpleNamespace(monotonic=lambda:now[0],sleep=sleep)):
                if allowed:backend_lifecycle.finish(lease,42,(99,1234))
                else:
                    with self.assertRaises(b.Stop):backend_lifecycle.finish(lease,42,(99,1234))
            self.assertEqual(closed,[99]);self.assertEqual(len(events),1)
            self.assertTrue(waits and all(0<=v<=2000 for v in waits))
            self.assertEqual(waits,sorted(waits,reverse=True))
            self.assertEqual(events[0]['total_budget_seconds'],2)
            if allowed:self.assertLessEqual(events[0]['elapsed_seconds'],2)
    def test_source_handle_metadata(self):
        import source_pin
        v=source_pin.Info();v.volume=1;v.index_lo=2;v.links=1;v.size_lo=4
        self.assertEqual(source_pin.facts(v,False)[:3],(1,0,2))
        for attribute,links,sizehi in ((0x400,1,0),(0x10,1,0),(0,0,0),(0,2,0),(0,1,1)):
            bad=source_pin.Info();bad.attributes=attribute;bad.links=links;bad.size_hi=sizehi
            with self.assertRaises(b.Stop):source_pin.facts(bad,False)
        v.attributes=0x10;self.assertEqual(source_pin.facts(v,True),(1,0,2))
    def test_pooled_service_rechecks_and_rolls_back(self):
        import types,threading
        from contextlib import ExitStack,contextmanager
        from unittest.mock import patch
        import transport
        calls=[];opens=[];tokens=[]
        class Connection:
            c=types.SimpleNamespace(info=types.SimpleNamespace(backend_pid=42))
            def execute(self,sql,args=()):
                calls.append(sql)
                return types.SimpleNamespace(fetchone=lambda:('expected_role',) if 'pg_catalog.pg_roles' in sql else ({'ok':True},))
        @contextmanager
        def connect(service):opens.append(service);yield Connection()
        job=types.SimpleNamespace(api=None,contains=lambda pid:pid==42,token=types.SimpleNamespace(process=lambda pid:tokens.append(pid)))
        plan=types.SimpleNamespace(postgres_executable='fixed.exe',run_id='a'*32)
        lease=types.SimpleNamespace(connect=connect,job=job,plan=plan,_backend_handles={42:(99,1234)})
        lab=transport.Lab.__new__(transport.Lab);lab.lease=lease;lab.pool_stack=ExitStack();lab.pool={};lab.pool_locks={'auth':threading.RLock()};lab.closing=False
        identity=types.SimpleNamespace(creation_token=1234,executable='fixed.exe')
        native=types.SimpleNamespace(_handle_identity=lambda *a:identity)
        roles=types.SimpleNamespace(role_identity_ok=lambda row,p,role:row==('expected_role',))
        with patch.dict(sys.modules,{'native_process':native,'runner':roles}):
            self.assertEqual(lab.database('auth','SELECT fixed_action'),{'ok':True})
            self.assertEqual(lab.database('auth','SELECT fixed_action'),{'ok':True})
            self.assertEqual(opens,['auth']);self.assertEqual(tokens,[42,42])
            identity.creation_token=9999
            with self.assertRaises(b.Stop):lab.database('auth','SELECT must_not_run')
            self.assertEqual(calls[-1],'ROLLBACK');self.assertNotIn('SELECT must_not_run',calls)
            lab.closing=True
            with self.assertRaises(b.Stop):lab.database('auth','SELECT must_not_run')
        lab.pool_stack.close()
    def test_epoch_uses_identical_policy_code_and_strict_identity(self):
        import copy,ipaddress
        import epoch_policy
        data=epoch_policy.record()
        guest=b.OLD/'continue_probe.py'
        source=guest if guest.exists() else Path(__file__).resolve().parents[3]/'outputs/pr03b/s3-batch3-snapshot-fix-20261108-01/snapshot/continue_probe.py'
        tree=ast.parse(source.read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='snapshot_topology_ok')
        old=frozenset((a['guid'],a['index'],a['type']) for a in data['original_plan']['before']['adapters'])
        ns={'re':re,'ipaddress':ipaddress,'_exact':lambda v,k:type(v) is dict and set(v)==set(k),'MACHINE_BINDING':epoch_policy.MACHINE,'ORIGINAL_ADAPTERS':old,'LOOPBACK_INDEX':1,'LOOPBACK_GUID':'31b6dc82-daff-11f1-80ff-806e6f6e6963'}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'frozen_policy','exec'),ns)
        original=ns['snapshot_topology_ok'];bound=epoch_policy.rebind(original)
        self.assertIs(bound.__code__,original.__code__);self.assertEqual(ns['ORIGINAL_ADAPTERS'],old)
        s=data['binding']['before'];self.assertFalse(original(s));self.assertTrue(bound(s))
        for field in ns:
            if field!='ORIGINAL_ADAPTERS':self.assertIs(bound.__globals__[field],ns[field])
        mutations=(
            lambda x:x['adapters'][0].update(admin=1),
            lambda x:x['adapters'][0].update(index=99),
            lambda x:x['adapters'][0].update(type=131),
            lambda x:x['adapters'][0].update(guid='11111111-1111-1111-1111-111111111111'),
            lambda x:x['adapters'].append(dict(x['adapters'][0])),
            lambda x:x['adapters'].pop(),
            lambda x:x.update(machine_binding='wrong'),
            lambda x:x['compartments'].append({'id':2,'description':'extra'}),
            lambda x:x['native_interfaces'][0].update(operational=2),
            lambda x:x['interfaces'].append({'index':13,'family':'IPv4','connected':1}),
            lambda x:x['routes'].append({'index':13,'destination':'192.0.2.1/32','next_hop':'0.0.0.0'}),
            lambda x:x['addresses'].pop())
        for mutate in mutations:
            changed=copy.deepcopy(s);mutate(changed);self.assertFalse(bound(changed))
    def test_WSS_negative_requires_terminal_protocol_evidence(self):
        import types,transport
        tree=ast.parse(Path(transport.__file__).read_text())
        fn=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='closed_as')
        calls=[]
        def check(label,ok,detail=None):
            calls.append((label,ok,detail))
            if not ok:raise b.Stop('terminal_evidence_required')
        class Closed(Exception):
            def __init__(self,code):self.rcvd=types.SimpleNamespace(code=code) if code else None
        ns={'ConnectionClosed':Closed,'check':check,'time':types.SimpleNamespace(monotonic=lambda:105.0)}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'WSS_negative','exec'),ns)
        def ws(error=None):
            def recv(**kw):
                if error:raise error
                return 'unexpected-message'
            return types.SimpleNamespace(recv=recv)
        f=ns['closed_as'];f(ws(Closed(1008)),'valid',{1008},started=100)
        self.assertEqual(calls[-1][2]['received_close_code'],1008)
        for error in (Closed(1009),Closed(None)):
            with self.assertRaises(b.Stop):f(ws(error),'wrong_close',{1008})
        with self.assertRaises(b.Stop):f(ws(),'message_is_not_close',{1008})
        before=len(calls)
        with self.assertRaises(TimeoutError):f(ws(TimeoutError()),'timeout_is_not_PASS',{1008})
        self.assertEqual(len(calls),before)
        with self.assertRaises(b.Stop):f(ws(Closed(1008)),'premature_close',{1008},started=104)
    def test_expiry_rechecks_follow_all_recovery_locks(self):
        sql=schema.extension('a'*32)
        write=sql[sql.index('CREATE FUNCTION identity_api.lab3_write'):sql.index('CREATE FUNCTION identity_api.lab3_status')]
        clock=write.index('now_at:=clock_timestamp()')
        for name in ('lab3_factors','lab3_groups'):
            self.assertLess(write.index('FROM identity_control.'+name+' WHERE owner=c.target ORDER BY generation FOR UPDATE'),clock)
        self.assertLess(clock,write.index('now_at<q.expires'))
        self.assertLess(write.index('now_at<q.expires'),write.index('UPDATE identity_control.lab3_factors'))
        status=sql[sql.index('CREATE FUNCTION identity_api.lab3_status'):sql.index('CREATE FUNCTION identity_api.lab3_step')]
        self.assertLess(status.index('WHERE id=oid AND case_id=cid AND executor=a FOR SHARE'),status.index('n:=clock_timestamp()'))
        self.assertLess(status.index('n:=clock_timestamp()'),status.index('n<q.expires'))
    def test_hash_queue_expiry_keeps_executing_reservations(self):
        import queue_cases
        sql=schema.extension('a'*32);start=sql.index('CREATE FUNCTION identity_api.lab3_hash_admission(');end=sql.index('$lab$;',start)
        body=sql[start:end];purge=body.index(queue_cases.CLEANUP)
        self.assertLess(body.index('pg_advisory_xact_lock'),purge)
        self.assertLess(body.index('n:=clock_timestamp()'),purge)
        self.assertLess(purge,body.index("SELECT count(*) FILTER(WHERE state='active')"))
        self.assertEqual(body.count(queue_cases.CLEANUP),1)
        self.assertNotIn("DELETE FROM identity_control.lab3_hash_jobs WHERE state='active'",body)

    def test_backend_exit_views_share_one_two_second_budget(self):
        import types,backend_lifecycle
        class Clock:
            def __init__(self):self.now=0
            def read(self):return self.now
            def sleep(self,seconds):self.now+=seconds
        class Kernel:
            def __init__(self,clock,wait=0,code=0,read_ok=True):self.clock=clock;self.wait=wait;self.code=code;self.read_ok=read_ok;self.waits=[]
            def WaitForSingleObject(self,handle,ms):
                self.waits.append(ms)
                if self.wait==258:self.clock.now+=ms/1000
                return self.wait
            def GetExitCodeProcess(self,handle,value):value._obj.value=self.code;return self.read_ok
        def attempt(states,*,wait=0,code=0,read_ok=True,observation_seconds=0):
            clock=Clock();k=Kernel(clock,wait,code,read_ok);pending=list(states)
            def observe():
                clock.now+=observation_seconds
                return pending.pop(0) if len(pending)>1 else pending[0]
            ok,rows=backend_lifecycle._observe_close(k,123,observe,clock.read,clock.sleep)
            self.assertTrue(all(0<=v<=2000 for v in k.waits))
            return ok,rows,clock.now
        ok,rows,elapsed=attempt([(True,True,0),(True,False,0)])
        self.assertTrue(ok);self.assertEqual(len(rows),2);self.assertTrue(rows[0]['in_active_job_list']);self.assertLessEqual(elapsed,2)
        self.assertTrue(attempt([(True,False,1),(True,False,0)])[0])
        for states,options in (([(True,True,0)],{}), ([(True,False,1)],{}),
                ([(True,False,0)],{'wait':258,'code':259}),
                ([(True,False,0)],{'wait':0xffffffff}),
                ([(True,False,0)],{'code':1}),
                ([(True,False,0)],{'read_ok':False}),
                ([(True,False,0)],{'observation_seconds':2.001})):
            self.assertFalse(attempt(states,**options)[0])
    def test_classifier_stable_20ms_gap_is_FAIL_and_sparse_is_not_PASS(self):
        import evidence_stats as es
        a=[300+(i%3)-1 for i in range(1000)];z=[320+(i%3)-1 for i in range(1000)]
        frozen=es.fit(a,z);observed=es.evaluate(a,z,frozen,240)
        self.assertEqual(observed['balanced_accuracy'],1)
        self.assertGreater(observed['BA_95_simultaneous'][0],.55)
        self.assertGreater(observed['AUC_star_95_simultaneous'][0],.55)
        self.assertEqual(es.auc(a,z),0)
        sparse=es.evaluate([1],[1],es.fit([1],[1]),240)
        self.assertGreater(sparse['BA_95_simultaneous'][1],.55)
        self.assertEqual(es.quantile_interval([1],.99),[1,None])
        low,high=es.quantile_interval(list(range(1000)),.5)
        self.assertLess(low,500);self.assertGreater(high,500)
    def test_journal_shards_preserve_complete_order_and_digest(self):
        import tempfile,hashlib,json,journal
        scope=b.HOME if Path(__file__).resolve().parent==b.HOME else Path(__file__).resolve().parents[3]/'work/pr03b'
        self.assertTrue(scope.is_dir())
        with tempfile.TemporaryDirectory(dir=scope) as folder:
            path=Path(folder)/'native-events.jsonl';log=journal.Events(path)
            values=[{'phase':'test','order':i,'value':'x'*500000} for i in range(20)]
            for row in values:log.append(row)
            result=log.finish();self.assertEqual(len(result['parts']),2)
            raw=b''.join((Path(folder)/p['file']).read_bytes() for p in result['parts'])
            self.assertEqual(hashlib.sha256(raw).hexdigest(),result['sha256'])
            self.assertEqual([json.loads(v) for v in raw.splitlines()],values)
            self.assertTrue(all(p['bytes']<=8*1024*1024 for p in result['parts']))
    def test_continuous_phase_boundaries_are_not_login_statuses(self):
        from load_driver import phase_for
        self.assertEqual([phase_for(v) for v in (0,999.9,1000,1599.9,1600,1659.9,1660,1779.9)],['normal','normal','overload','overload','transition','transition','recovery','recovery'])
    def test_enumeration_missing_cells_and_shared_units_never_PASS(self):
        import enumeration_plan as ep
        design=ep.plan(1234);result=ep.analyze([],design)
        self.assertEqual(result['result'],'INCONCLUSIVE');self.assertFalse(result['no_enumeration_PASS'])
        self.assertEqual(len(result['branches']),design['family_size'])
        row={'trial':'t1','target':'a','time_block':'b1','split':'calibration','arm':'known','cell':0,'context_count':1,'budget_resets':0,'sequence':[]}
        for bad in (dict(row,trial='t2'),dict(row,trial='t2',target='different'),dict(row,arm='unknown',target='different',split='holdout'),dict(row,budget_resets=1)):
            with self.assertRaises(AssertionError):ep.validate_units([row,bad],design)
        self.assertFalse(design['sample_reduction_allowed'])
    def test_lost_admission_commit_only_reclaims_unstarted_owned_task(self):
        import admission,time
        for state,expected in (('active',['poll','finish']),('queued',['poll','cancel']),('rejected',['poll'])):
            calls=[]
            def control(mode):
                self.assertGreater(admission.remaining(),0);calls.append(mode)
                return {'state':state}
            token=admission.CURRENT.set(time.monotonic()-1)
            try:admission.reclaim_unstarted(control,expensive_calls=0)
            finally:admission.CURRENT.reset(token)
            self.assertEqual(calls,expected)
        with self.assertRaises(b.Stop):admission.reclaim_unstarted(lambda m:self.fail('running hash must remain owned'),expensive_calls=1)

    def test_unstarted_cleanup_retries_only_rolled_back_lock_timeout_with_one_deadline(self):
        import admission,time
        from unittest.mock import patch
        class LockTimeout(Exception):sqlstate='55P03'
        calls=[];deadlines=[]
        def control(mode):
            calls.append(mode);deadlines.append(admission.CURRENT.get())
            if len(calls)<=2:raise LockTimeout()
            return {'state':'active'}
        with patch.object(admission.time,'sleep',return_value=None):
            admission.reclaim_unstarted(control,expensive_calls=0)
        self.assertEqual(calls,['poll','poll','poll','finish'])
        self.assertEqual(len(set(deadlines)),1)
        class Denied(Exception):sqlstate='42501'
        with self.assertRaises(Denied):admission.reclaim_unstarted(lambda m:(_ for _ in ()).throw(Denied()),expensive_calls=0)
        clock=[100.0]
        def fail(mode):clock[0]+=2;raise LockTimeout()
        with patch.object(admission.time,'monotonic',side_effect=lambda:clock[0]),patch.object(admission.time,'sleep',return_value=None):
            with self.assertRaises(admission.Expired):admission.reclaim_unstarted(fail,expensive_calls=0)
    def test_reduced_child_checks_both_revocation_flags_before_signer_access(self):
        import types
        tree=ast.parse((Path(__file__).parent/'secure_child.py').read_bytes())
        verify=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='verify')
        signatures=[]
        ns={'b':b,'installed':{'os_subject':'sid','deployment':'lab'},'windows':types.SimpleNamespace(sid_string=lambda:'sid'),'pin':types.SimpleNamespace(verify=lambda *args:signatures.append(True)),'crypto':types.SimpleNamespace(unb64=lambda *args:b'',signed=lambda *args:b''),'signer':None}
        exec(compile(ast.Module(body=[verify],type_ignores=[]),'isolated_child_verifier','exec'),ns)
        for on_disk,on_wire in ((False,False),(False,True),(True,False)):
            ns['manifest']={'deployment':'lab','enabled':on_disk}
            with self.assertRaisesRegex(b.Stop,'D0_revoked'):ns['verify']({'current':{'deployment':'lab','enabled':on_wire},'signature':'synthetic'})
        self.assertEqual(len(signatures),3)
        ns['manifest']={'deployment':'lab','enabled':True};ns['ledger']={'phase':'reserved'}
        for phase in ('reserved','ready'):
            with self.assertRaisesRegex(b.Stop,'D0_pending_transition'):ns['verify']({'current':{'deployment':'lab','enabled':True,'phase':phase},'signature':'synthetic'})

    def test_queue_recovery_requires_ten_seconds_not_one_zero_sample(self):
        from capacity import queue_recovery
        rows=[{'monotonic':float(i),'queued':5 if 1000<=i<1620 else 0} for i in range(1781)]
        okay=queue_recovery(rows,0);self.assertLessEqual(okay['first_10s_stable_window_end_seconds'],60);self.assertTrue(okay['recovery_windows_meet_baseline'])
        bad=[{'monotonic':float(i),'queued':5 if 1000<=i<1666 and i!=1601 else 0} for i in range(1781)]
        result=queue_recovery(bad,0);self.assertGreater(result['first_10s_stable_window_end_seconds'],60);self.assertFalse(result['recovery_windows_meet_baseline'])
        gap=[r for r in rows if not 1700<=r['monotonic']<1720]
        self.assertFalse(queue_recovery(gap,0)['recovery_2min_covered'])

    def test_concurrent_first_use_has_one_shared_kernel_slot_factory(self):
        import admission,threading,time
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        barrier=threading.Barrier(8);created=[]
        def factory():
            time.sleep(.02);value=object();created.append(value);return value
        def call(_):barrier.wait(5);return admission.shared()
        with patch.object(admission,'_shared',None),patch.object(admission,'SharedSlots',factory):
            with ThreadPoolExecutor(max_workers=8) as workers:values=list(workers.map(call,range(8)))
        self.assertEqual(len(created),1);self.assertTrue(all(v is created[0] for v in values))

    def test_load_HTTP_adapter_constructs_request_and_always_closes(self):
        import types,uuid
        tree=ast.parse((Path(__file__).parent/'load_driver.py').read_bytes())
        fn=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='call_http')
        calls=[];closed=[];fail=[]
        class Connection:
            def __init__(self,*args,**kwargs):calls.append(('connect',args,kwargs))
            def request(self,*args):calls.append(('request',args))
            def getresponse(self):
                if fail:raise TimeoutError('synthetic')
                return types.SimpleNamespace(status=200,read=lambda:b'{"ok":true}',getheaders=lambda:[('X-Test','safe')])
            def close(self):closed.append(True)
        ns={'http':types.SimpleNamespace(client=types.SimpleNamespace(HTTPSConnection=Connection)),'cfg':{'port':12345,'origin':'https://app.test'},'ctx':object(),'json':json,'uuid':uuid}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'isolated_HTTP_adapter','exec'),ns)
        self.assertEqual(ns['call_http']('/lab/preauth'),(200,{'ok':True},{'X-Test':'safe'}))
        self.assertEqual(calls[-1][1][:3],('GET','/lab/preauth',None))
        ns['call_http']('/lab/ws-ticket',{}, {'X-Test':'bound'})
        self.assertEqual(calls[-1][1][:3],('POST','/lab/ws-ticket',b'{}'))
        fail.append(True);request_ids=[]
        with self.assertRaises(TimeoutError):ns['call_http']('/lab/preauth',request_ids=request_ids)
        self.assertEqual(len(request_ids),1)
        self.assertEqual(calls[-1][1][3]['X-Lab-Request-ID'],request_ids[0])
        self.assertEqual(len(closed),3)

    def test_R05_obsolete_sampler_is_closed_and_prerequisites_are_explicit(self):
        import enumeration,enumeration_plan as ep
        from unittest.mock import patch
        with patch.object(enumeration.transport,'Lab',side_effect=AssertionError('must not start lab')):
            with self.assertRaisesRegex(b.Stop,'R05_full_collector_not_validated'):
                enumeration.experiments(None,None,None)
        design=ep.plan(1234)
        result=ep.readiness(design,None,actual_account_failure_limit=3)
        self.assertFalse(result['execution_ready']);self.assertEqual(result['result'],'INCONCLUSIVE')
        self.assertIn('admitted_k4_conflicts_with_actual_account_failure_budget',result['reasons'])
        self.assertFalse(result['budget_or_threshold_modified'])
        self.assertEqual(result['minimum_independent_arm_sequences'],432000)
        self.assertGreater(result['AUC_bound_half_width_at_N1000_per_arm'],.05)
        self.assertFalse(ep.readiness(design,{'result':'PASS'},actual_account_failure_limit=5)['execution_ready'])

    def test_source_readers_verify_concurrently_without_reusing_bytes(self):
        import source_pin,queue,threading,types,time,admission
        from concurrent.futures import ThreadPoolExecutor
        bundle=source_pin.Bundle.__new__(source_pin.Bundle)
        bundle.lock=threading.RLock();bundle.cv=threading.Condition(bundle.lock)
        bundle.closed=False;bundle.closing=False;bundle.active=0;bundle.borrowed=0;bundle.peak_borrowed=0;bundle.completed=0
        bundle.home=Path('synthetic-no-file-io');bundle.digest='synthetic';bundle.directories=[];bundle.handles=[]
        payload=b'full immutable synthetic source';identity=(1,2,3)
        bundle.files={'fixture':(0,identity,hashlib.sha256(payload).hexdigest())}
        bundle.readers=queue.Queue()
        for handle in (71,72):bundle.readers.put({'files':{'fixture':handle},'data':None,'name':None})
        bundle.named=lambda *args:None;bundle.info=lambda *args:identity
        barrier=threading.Barrier(2);calls=[];bad=[]
        def content(handle,limit,scratch):
            calls.append(handle)
            if len(calls)<=2:barrier.wait(2)
            return b'changed' if bad else payload
        bundle.content=content;bundle.k=types.SimpleNamespace(CloseHandle=lambda h:True)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:bundle.verify(),range(2)))
        self.assertEqual(results,['synthetic']*2);self.assertEqual(set(calls),{71,72})
        self.assertEqual(bundle.peak_borrowed,2);self.assertEqual(bundle.completed,2)
        bad.append(True)
        with self.assertRaisesRegex(b.Stop,'pinned_source_content_changed'):bundle.verify()
        self.assertEqual(len(calls),3);self.assertEqual(bundle.completed,2)
        self.assertEqual(bundle.borrowed,0);self.assertEqual(bundle.active,0)
        # Exhausted verifier channels obey the existing absolute deadline.
        held=[bundle.readers.get(),bundle.readers.get()];token=admission.CURRENT.set(time.monotonic()+.02)
        try:
            with self.assertRaisesRegex(admission.Expired,'source_pin_reader_deadline'):bundle.verify()
        finally:
            admission.CURRENT.reset(token)
            for row in held:bundle.readers.put(row)
        self.assertEqual(bundle.active,0);bundle.close()
        with self.assertRaisesRegex(b.Stop,'source_pin_already_closed'):bundle.verify()

    def test_source_close_waits_for_actual_reader_completion(self):
        import source_pin,threading,queue,types,time
        bundle=source_pin.Bundle.__new__(source_pin.Bundle)
        bundle.lock=threading.RLock();bundle.cv=threading.Condition(bundle.lock)
        bundle.closed=False;bundle.closing=False;bundle.active=0;bundle.borrowed=0;bundle.peak_borrowed=0;bundle.completed=0
        bundle.home=Path('synthetic-no-file-io');bundle.digest='synthetic';bundle.directories=[];bundle.handles=[123]
        payload=b'source';identity=(1,2,3);bundle.files={'fixture':(123,identity,hashlib.sha256(payload).hexdigest())}
        bundle.readers=queue.Queue();bundle.readers.put({'files':{'fixture':71},'data':None,'name':None})
        bundle.named=lambda *args:None;bundle.info=lambda *args:identity
        entered=threading.Event();release=threading.Event();closed=[];values=[];errors=[]
        def content(*args):entered.set();self.assertTrue(release.wait(2));return payload
        bundle.content=content;bundle.k=types.SimpleNamespace(CloseHandle=lambda h:closed.append(h) or True)
        def invoke(fn):
            try:values.append(fn())
            except BaseException as ex:errors.append(type(ex).__name__)
        reader=threading.Thread(target=invoke,args=(bundle.verify,));reader.start();self.assertTrue(entered.wait(2))
        closer=threading.Thread(target=invoke,args=(bundle.close,));closer.start()
        end=time.monotonic()+1
        while not bundle.closing and time.monotonic()<end:time.sleep(.001)
        self.assertTrue(bundle.closing);self.assertFalse(bundle.closed);self.assertEqual(closed,[])
        release.set();reader.join(2);closer.join(2)
        self.assertFalse(reader.is_alive());self.assertFalse(closer.is_alive());self.assertEqual(errors,[])
        self.assertTrue(bundle.closed);self.assertEqual(closed,[123]);self.assertIn('synthetic',values)

    def test_historical_stderr_read_budget_is_exact_and_does_not_skip_hashing(self):
        self.assertEqual(b.preserved_read_limit('coordinator.stderr.log',{'bytes':101169845,'sha256':'a'*64}),128*1024*1024)
        self.assertEqual(b.preserved_read_limit('other.log',{'bytes':101169845,'sha256':'a'*64}),16*1024*1024)
        for pin in ({'bytes':128*1024*1024+1,'sha256':'a'*64},{'bytes':True,'sha256':'a'*64},{'bytes':-1,'sha256':'a'*64}):
            with self.assertRaises(b.Stop):b.preserved_read_limit('coordinator.stderr.log',pin)

    def test_TLS_accept_is_deferred_bounded_and_errors_do_not_serialize_secrets(self):
        import types,threading,collections,time,traceback
        tree=ast.parse((Path(__file__).parent/'transport.py').read_bytes())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='TLSHTTPServer')
        events=[]
        class Peer:
            fail=False
            def settimeout(self,value):events.append(('timeout',value))
            def do_handshake(self):
                events.append(('handshake',))
                if self.fail:raise TimeoutError('SYNTHETIC_PRIVATE_SENTINEL')
            def close(self):events.append(('closed',))
        peer=Peer()
        class Base:
            def __init__(self,*args):pass
            def get_request(self):return peer,('127.0.0.1',12345)
            def process_request(self,*args):return self.process_request_thread(*args)
            def process_request_thread(self,*args):events.append(('application',));self.shutdown_request(args[0])
            def shutdown_request(self,*args):events.append(('shutdown',))
        def wrap(raw,**kwargs):
            self.assertEqual(kwargs,{'server_side':True,'do_handshake_on_connect':False});events.append(('wrapped',));return raw
        ns={'http':types.SimpleNamespace(server=types.SimpleNamespace(ThreadingHTTPServer=Base)),
            'threading':threading,'collections':collections,'sys':sys,'traceback':traceback,'Path':Path,'time':time,'b':b}
        exec(compile(ast.Module(body=[cls],type_ignores=[]),'isolated_TLS_transport','exec'),ns)
        server=ns['TLSHTTPServer'](None,None,types.SimpleNamespace(wrap_socket=wrap))
        request,address=server.get_request();self.assertNotIn(('handshake',),events)
        server.process_request(request,address);server.wait_drained()
        self.assertEqual(server.diagnostics()['active_workers'],0)
        self.assertEqual(server.transport_slots._value,64)
        peer.fail=True;server.process_request(request,address);server.wait_drained()
        diagnostics=server.diagnostics();self.assertEqual(diagnostics['error_counts'],{'TimeoutError':1})
        self.assertNotIn('SYNTHETIC_PRIVATE_SENTINEL',json.dumps(diagnostics))
        server.transport_slots=threading.BoundedSemaphore(1);self.assertTrue(server.transport_slots.acquire(False))
        server.process_request(request,address);self.assertEqual(server.active_workers,0)
        self.assertEqual(server.diagnostics()['error_counts']['transport_capacity'],1)
        self.assertEqual(events.count(('handshake',)),2)

def run():
    stream=io.StringIO();r=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Checks))
    return dict(passed=r.wasSuccessful() and not r.skipped,tests=r.testsRun,failures=len(r.failures),errors=len(r.errors),skipped=len(r.skipped),database_connections=0,postgres_started=False,evidence_level='synthetic_and_owned_process_metadata_no_PG',log=stream.getvalue())
if __name__=='__main__':
    r=run();print(json.dumps(r));raise SystemExit(0 if r['passed'] else 1)
