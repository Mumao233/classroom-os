"""Cache immutable bindings and pure syntax parameters, never observations.

Every caller gets a fresh API object. Per-Job kernel/token/resource proxies
therefore cannot escape into another Job or bypass its reduced-token creation.
"""
import ctypes,os,threading,functools
import common as b

class ShortCalls:
    """Keep the GIL only across bounded local metadata queries on Win64.

    Releasing/reacquiring it for each tiny query causes contention between
    observers. Process creation, waits, file/pipe I/O and all unlisted calls
    keep their original ctypes behavior. Every query still executes afresh.
    """
    def __init__(self,original,dll,names):
        self.original=original;self.calls={}
        library=ctypes.PyDLL('C:\\Windows\\System32\\'+dll,use_last_error=True)
        for name in names:
            old=getattr(original,name);new=getattr(library,name)
            new.argtypes=old.argtypes;new.restype=old.restype;self.calls[name]=new
    def __getattr__(self,name):
        return self.calls[name] if name in self.calls else getattr(self.original,name)

class GuardLibraries:
    """Module-local binding cache for the unchanged original observer code."""
    def __init__(self,original):self.original=original;self.local=threading.local()
    def __getattr__(self,name):return getattr(self.original,name)
    def WinDLL(self,name,*args,**kwargs):
        if name!='kernel32' or args or kwargs!={'use_last_error':True}:return self.original.WinDLL(name,*args,**kwargs)
        library=getattr(self.local,'kernel',None)
        if library is None:
            b.require(os.name=='nt' and ctypes.sizeof(ctypes.c_void_p)==8,'guard_metadata_binding_win64')
            # The frozen guard uses this factory only for bounded process
            # identity metadata (OpenProcess/query/CloseHandle). No wait,
            # creation or file/pipe operation is routed through this library.
            library=self.original.PyDLL(r'C:\Windows\System32\kernel32.dll',**kwargs);self.local.kernel=library
        return library

class SyntaxPaths:
    """Bounded memoization of pure string syntax parsing, with no stat/open.

    Exact string inputs only. Other types retain original rejection behavior.
    Physical paths, process identity and every filesystem check remain fresh.
    """
    def __init__(self,guard):
        self.guard=guard;self.original=guard._absolute;self.path_type=guard.Path;self.platform=os.name
        self.cached=functools.lru_cache(maxsize=256)(self.original)
    def __call__(self,value):
        b.require(os.name==self.platform and self.guard.Path is self.path_type,'path_syntax_context_changed')
        return self.cached(value) if type(value) is str else self.original(value)

class Cache:
    def __init__(self,native,guard=None):
        self.native=native;self.original=native._Api;self.local=threading.local();self.lock=threading.Lock();self.calls=0;self.initializations=0
        self.guard=guard;self.guard_ctypes=None
        if guard is not None:
            self.guard_ctypes=guard.ctypes;self.guard_proxy=GuardLibraries(self.guard_ctypes)
            self.absolute=SyntaxPaths(guard)
        self.identity=None
        # Unit fixtures do not install a live OS observer. A real runtime uses
        # only the source-pinned, query-only DLL; no unavailable-DLL fallback.
        if guard is not None and hasattr(guard,'observe_windows_process'):
            import native_identity
            self.identity=native_identity.Library();self.original_observer=guard.observe_windows_process;self.original_handle_observer=native._handle_identity
            owner=self
            def observe_pid(pid):
                try:created,path=owner.identity.pid(pid)
                except native_identity.Rejected as error:guard._reject('native process identity unavailable:'+str(error))
                return guard.ProcessIdentity(pid,created,guard._absolute(path))
            def observe_handle(api,handle,pid):
                native._pid(pid)
                try:created,path=owner.identity.handle(handle,pid)
                except native_identity.Rejected as error:native._fail('live process identity unavailable:'+str(error))
                return guard.ProcessIdentity(pid,created,guard._absolute(path))
            observe_pid.s3_identity_bridge=self.identity;observe_pid.s3_original_observer=self.original_observer;observe_pid.s3_original_handle_observer=self.original_handle_observer
            self.observer=observe_pid;self.handle_observer=observe_handle
        owner=self;original=self.original
        class Bound(original):
            def __init__(self):
                b.require(os.name=='nt' and ctypes.sizeof(ctypes.c_void_p)==8,'native_bindings_windows_x64')
                libraries=getattr(owner.local,'libraries',None)
                if libraries is None:
                    temporary=original()
                    b.require(set(vars(temporary))=={'k','ip','nt','sh'},'native_bindings_only_libraries')
                    # The synthetic Library fixture has no DLL handle. Do
                    # not convert mocks into actual OS observations.
                    if isinstance(temporary.k,ctypes.WinDLL):
                        temporary.k=ShortCalls(temporary.k,'kernel32.dll',('OpenProcess','GetProcessId','GetProcessTimes','QueryFullProcessImageNameW','IsProcessInJob','QueryInformationJobObject'))
                        temporary.nt=ShortCalls(temporary.nt,'ntdll.dll',('NtQueryInformationProcess',))
                    libraries=(temporary.k,temporary.ip,temporary.nt,temporary.sh);owner.local.libraries=libraries
                    with owner.lock:owner.initializations+=1
                self.k,self.ip,self.nt,self.sh=libraries
                with owner.lock:owner.calls+=1
        self.bound=Bound;native._Api=Bound
        if guard is not None:
            guard.ctypes=self.guard_proxy;guard._absolute=self.absolute
        if self.identity is not None:
            guard.observe_windows_process=self.observer;native._handle_identity=self.handle_observer
    def statistics(self):
        with self.lock:return {'API_objects_created':self.calls,'thread_binding_initializations':self.initializations,'only_bindings_and_pure_syntax_parameters_cached':True,'pure_path_syntax_cache_entries':self.absolute.cached.cache_info().currsize if self.guard is not None else 0,'original_guard_observer_DLL_bindings_cached':self.guard is not None,'API_instances_shared':False,'process_network_role_or_authorization_results_cached':False,'filesystem_observations_cached':False,'native_identity':self.identity.statistics() if self.identity is not None else None}
    def close(self):
        b.require(self.native._Api is self.bound,'native_bindings_dispatch_changed')
        if self.identity is not None:
            b.require(self.guard.observe_windows_process is self.observer and self.native._handle_identity is self.handle_observer,'native_identity_dispatch_changed')
        if self.guard is not None:
            b.require(self.guard.ctypes is self.guard_proxy,'guard_ctypes_dispatch_changed')
            b.require(self.guard._absolute is self.absolute,'guard_syntax_dispatch_changed')
        self.native._Api=self.original
        if self.guard is not None:
            self.guard.ctypes=self.guard_ctypes;self.guard._absolute=self.absolute.original;self.absolute.cached.cache_clear()
        if self.identity is not None:
            self.guard.observe_windows_process=self.original_observer;self.native._handle_identity=self.original_handle_observer;self.identity.close()

