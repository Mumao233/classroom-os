"""Fresh reads of five immutable guard inputs through held Win32 handles.

The original guard functions still run at every boundary. No bytes, hashes,
process facts or decisions are cached. Mutable owner/pid files and every other
path retain the original reader. Pins additionally deny write/delete opens.
"""
import ctypes as c,hashlib,queue,threading,time,os,mmap
from ctypes import wintypes as w
from pathlib import Path
import admission,common as b
from source_pin import Info,facts

class Buffer(c.Structure):
    _fields_=[('buf',c.c_void_p),('obj',c.c_void_p),('length',c.c_ssize_t),('itemsize',c.c_ssize_t),('readonly',c.c_int),('ndim',c.c_int),('format',c.c_void_p),('shape',c.c_void_p),('strides',c.c_void_p),('suboffsets',c.c_void_p),('internal',c.c_void_p)]

class ReadOnlyView(mmap.mmap):
    """Live immutable-file pages, with complete native byte comparisons.

    The original SHA checks read this buffer directly. Each equality invokes
    CRT memcmp with the complete length, including self-comparison; the CRT
    can optimize identical pointers or stop at a mismatch. Holding Py_buffer
    exports prevents unmapping during the native comparison.
    """
    _api=None;_lock=threading.Lock();_calls=0;_bytes=0
    def __new__(cls,fd):return super().__new__(cls,fd,0,access=mmap.ACCESS_READ)
    def __eq__(self,other):
        if not isinstance(other,(bytes,ReadOnlyView)):return NotImplemented
        with self._lock:
            if self._api is None:
                b.require(c.sizeof(Buffer)==80,'guard_io_buffer_layout')
                get=c.pythonapi.PyObject_GetBuffer;get.argtypes=[c.py_object,c.POINTER(Buffer),c.c_int];get.restype=c.c_int
                release=c.pythonapi.PyBuffer_Release;release.argtypes=[c.POINTER(Buffer)];release.restype=None
                library=c.CDLL(r'C:\Windows\System32\ucrtbase.dll');compare=library.memcmp;compare.argtypes=[c.c_void_p,c.c_void_p,c.c_size_t];compare.restype=c.c_int
                type(self)._api=(get,release,compare,library)
        get,release,compare,_=self._api;left=Buffer();right=Buffer()
        try:
            get(self,c.byref(left),0);get(other,c.byref(right),0)
            b.require(left.readonly==right.readonly==1 and left.length>=0 and right.length>=0,'guard_io_readonly_buffers')
            if left.length!=right.length:return False
            result=compare(left.buf,right.buf,left.length)==0
            with self._lock:type(self)._calls+=1;type(self)._bytes+=left.length
            return result
        finally:
            if right.obj:release(c.byref(right))
            if left.obj:release(c.byref(left))
    def __ne__(self,other):
        result=self.__eq__(other)
        return NotImplemented if result is NotImplemented else not result

class Reads:
    def __init__(self,guard,plan,pins):
        b.require(c.sizeof(Info)==52,'guard_io_native_layout')
        self.guard=guard;self.original=guard._read_file;self.plan=plan
        self.pins={plan.postgres_executable:plan.postgres_sha256,**{plan.data_directory/name:digest for name,digest in pins.items()}}
        b.require(set(pins)=={'postgresql.conf','pg_hba.conf','pg_ident.conf','postgresql.auto.conf'} and len(self.pins)==5,'guard_io_exact_inputs')
        self.lock=threading.RLock();self.cv=threading.Condition(self.lock);self.active=0;self.closed=False;self.closing=False;self.native_files=None;self.native_entries=[]
        self.handles=[];self.streams=[];self.views=[];self.shared_views={};self.directories=[];self.files={};self.ancestors={};self.readers=queue.Queue(16);self.calls=0;self.bytes=0;self.opened_names=False
        self.k=c.WinDLL('kernel32',use_last_error=True)
        bindings={'CreateFileW':([w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE],w.HANDLE),
            'CloseHandle':([w.HANDLE],w.BOOL),'GetFileInformationByHandle':([w.HANDLE,c.POINTER(Info)],w.BOOL),
            'GetHandleInformation':([w.HANDLE,c.POINTER(w.DWORD)],w.BOOL),'GetFinalPathNameByHandleW':([w.HANDLE,w.LPWSTR,w.DWORD,w.DWORD],w.DWORD)}
        for name,(args,result) in bindings.items():fn=getattr(self.k,name);fn.argtypes=args;fn.restype=result
        from native_bindings import ShortCalls
        self.k=ShortCalls(self.k,'kernel32.dll',('GetFileInformationByHandle','GetFinalPathNameByHandleW'))
        try:
            directories={parent for path in self.pins for parent in path.parents}
            for path in sorted(directories,key=lambda p:(len(p.parts),str(p))):
                guard.no_reparse(path);h=self.open(path,True)
                self.named(h,path,c.create_unicode_buffer(32768))
                self.directories.append((h,self.info(h,True),path))
            for path,digest in self.pins.items():
                raw=self.original(path,64*1024*1024)
                b.require(hashlib.sha256(raw).hexdigest()==digest,'guard_io_initial_digest')
                h=self.open(path,False);identity=self.info(h,False)
                b.require(((identity[4]<<32)|identity[5])==len(raw),'guard_io_initial_size')
                self.files[path]=(identity,len(raw),digest)
                self.ancestors[path]=[v for v in self.directories if v[2] in path.parents]
            import msvcrt
            for _ in range(16):
                members={path:self.open(path,False) for path in self.files}
                views={}
                for path,h in members.items():
                    # Original metadata/name checks and deny-WRITE/DELETE
                    # handles cover the entire mapping lifetime. These are OS
                    # file pages, not a saved validated byte/hash snapshot.
                    if path not in self.shared_views:
                        fd=msvcrt.open_osfhandle(h,os.O_RDONLY|os.O_BINARY|os.O_NOINHERIT);self.handles.remove(h)
                        try:stream=os.fdopen(fd,'rb',buffering=0)
                        except BaseException:os.close(fd);raise
                        self.streams.append(stream);view=ReadOnlyView(stream.fileno());self.views.append(view);self.shared_views[path]=view
                    # Immutable buffers have no seek cursor. Sharing the OS
                    # view does not share a validated digest or native fact;
                    # the original SHA still reads all bytes every boundary.
                    # Independent handles still check each caller's file ID.
                    views[path]=self.shared_views[path]
                self.readers.put({'members':members,'views':views,'name':c.create_unicode_buffer(32768)})
            # Each independently opened cursor is checked against original
            # safe reader bytes before replacing the reader dispatch.
            for path,(_,_,digest) in self.files.items():
                current=self.read(path,64*1024*1024)
                b.require(hashlib.sha256(current).hexdigest()==digest,'guard_io_pinned_digest')
                b.require(current==self.original(path,64*1024*1024),'guard_io_initial_equivalence')
            guard._read_file=self.read
            self.opened_names=True
            import native_files
            self.native_files=native_files.Library()
            readers=[]
            while not self.readers.empty():readers.append(self.readers.get_nowait())
            for reader in readers:
                reader['native_inputs']={}
                for path,(identity,_,_) in self.files.items():
                    ancestors=[(h,expected,None,None,None) for h,expected,_ in self.ancestors[path]]
                    file=(reader['members'][path],identity,path,None,None)
                    entry=native_files.Entries([*ancestors,file,file,*ancestors]);entry.array[len(ancestors)+1].path_after=1
                    reader['native_inputs'][path]=entry;self.native_entries.append(entry)
                self.readers.put(reader)
        except BaseException:self.close();raise
    def info(self,h,directory):
        value=Info();b.require(self.k.GetFileInformationByHandle(h,c.byref(value)),'guard_io_metadata')
        return facts(value,directory)
    def named(self,h,path,buffer):
        size=self.k.GetFinalPathNameByHandleW(h,buffer,len(buffer),8 if getattr(self,'opened_names',False) else 0)
        b.require(0<size<len(buffer) and buffer.value.casefold()==('\\\\?\\'+str(path)).casefold(),'guard_io_named_path')
    def open(self,path,directory):
        h=self.k.CreateFileW(str(path),0x81 if directory else 0x80000000,3 if directory else 1,None,3,0x00200000|(0x02000000 if directory else 0),None)
        b.require(h not in (None,c.c_void_p(-1).value),'guard_io_pin_open')
        try:
            flags=w.DWORD();b.require(self.k.GetHandleInformation(h,c.byref(flags)) and not flags.value&1,'guard_io_noninheritable')
            self.info(h,directory);self.handles.append(h);return h
        except BaseException:self.k.CloseHandle(h);raise
    def read(self,path,limit):
        path=Path(path)
        if path not in self.files:return self.original(path,limit)
        identity,size,digest=self.files[path]
        b.require(type(limit) is int and size<=limit,'guard_io_read_budget')
        with admission.locked(self.lock):
            b.require(not self.closed and not self.closing,'guard_io_closed');self.active+=1
        reader=None
        try:
            wait=admission.live()
            try:reader=self.readers.get() if wait is None else self.readers.get(timeout=wait)
            except queue.Empty:raise admission.Expired('guard_io_reader_deadline') from None
            if getattr(self,'native_files',None) is not None:
                self.native_files.check(reader['native_inputs'][path],reader['name'])
                raw=reader['views'][path];b.require(len(raw)==size,'guard_io_fresh_read')
                with self.lock:self.calls+=1;self.bytes+=size
                return raw
            for h,expected,parent in self.ancestors[path]:
                # Ancestor names cannot be renamed/deleted while their held
                # handles deny DELETE. Fresh identity/reparse checks remain
                # before and after every input, as in the original reader.
                b.require(self.info(h,True)==expected,'guard_io_ancestor_changed')
            h=reader['members'][path];self.named(h,path,reader['name'])
            b.require(self.info(h,False)==identity,'guard_io_file_changed')
            raw=reader['views'][path];b.require(len(raw)==size,'guard_io_fresh_read')
            b.require(self.info(h,False)==identity,'guard_io_changed_during_read');self.named(h,path,reader['name'])
            for ancestor,expected,parent in self.ancestors[path]:
                b.require(self.info(ancestor,True)==expected,'guard_io_ancestor_changed_after_read')
            admission.live()
            with self.lock:self.calls+=1;self.bytes+=size
            return raw
        finally:
            if reader is not None:self.readers.put(reader)
            with self.lock:self.active-=1;self.cv.notify_all()
    def deny_probe(self):
        rows=[]
        for path in self.pins:
            for access in (0x40000000,0x10000):
                c.set_last_error(0);h=self.k.CreateFileW(str(path),access,7,None,3,0x00200000,None);error=c.get_last_error()
                if h not in (None,c.c_void_p(-1).value):
                    self.k.CloseHandle(h);raise b.Stop('guard_io_write_or_delete_allowed')
                b.require(error==32,'guard_io_expected_sharing_violation')
                rows.append({'input':'postgres.exe' if path==self.plan.postgres_executable else path.name,'access':'WRITE' if access==0x40000000 else 'DELETE','winerror':error})
        return rows
    def statistics(self):
        with self.lock:return {'pinned_inputs':5,'checked_buffer_views':self.calls,'bytes_exposed_to_original_hash_and_comparison':self.bytes,'native_comparison_calls':ReadOnlyView._calls,'input_bytes_of_native_comparisons':ReadOnlyView._bytes,'shared_immutable_file_views':5,'active':self.active,'validated_byte_snapshot_or_observation_cache':False,'read_representation':'shared live readonly OS file pages; full original SHA every boundary and full-length native comparisons','comparison_runtime':r'C:\Windows\System32\ucrtbase.dll','original_mutable_file_reader_retained':True,'independent_file_objects':16}
    def close(self):
        with self.lock:
            if self.closed:return
            self.closing=True;until=time.monotonic()+5
            while self.active:
                remain=until-time.monotonic();b.require(remain>0,'guard_io_not_drained');self.cv.wait(remain)
            # Do not replace someone else's dispatch during failed setup.
            if self.guard._read_file==self.read:self.guard._read_file=self.original
            okay=True
            if getattr(self,'native_files',None) is not None:
                self.native_files.close()
                for entry in self.native_entries:entry.close()
            for view in self.views:
                try:view.close()
                except (OSError,BufferError):okay=False
            for stream in self.streams:
                try:stream.close()
                except OSError:okay=False
            for h in reversed(self.handles):okay=bool(self.k.CloseHandle(h)) and okay
            self.closed=True;b.require(okay,'guard_io_handle_cleanup')
