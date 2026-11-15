"""Guest-only immutable source handles; verify all bytes at each boundary.

Directory ancestors cannot be renamed while held. Source files additionally
deny writable/deleting opens. No ACL, registry, original helper or OS policy is
changed. These handles are non-inheritable and belong only to this coordinator.
"""
import ctypes as c,hashlib,json,os,threading,queue,time,mmap
from ctypes import wintypes as w
from pathlib import Path
import common as b
import admission
class Info(c.Structure):
    _fields_=[('attributes',w.DWORD),('created',w.FILETIME),('accessed',w.FILETIME),('written',w.FILETIME),('volume',w.DWORD),('size_hi',w.DWORD),('size_lo',w.DWORD),('links',w.DWORD),('index_hi',w.DWORD),('index_lo',w.DWORD)]
def facts(info,directory):
    b.require(not info.attributes&0x400 and bool(info.attributes&0x10)==directory,'pinned_reparse_or_kind')
    if not directory:b.require(info.links==1 and ((info.size_hi<<32)|info.size_lo)<=256*1024*1024,'pinned_file_links_budget')
    identity=(info.volume,info.index_hi,info.index_lo)
    return identity if directory else identity+(info.attributes,info.size_hi,info.size_lo,info.links,info.written.dwHighDateTime,info.written.dwLowDateTime)
class Bundle:
    def __init__(self,home,digest):
        b.require(os.name=='nt' and c.sizeof(Info)==52 and home==b.HOME,'guest_source_pin_shape')
        self.home=home;self.digest=digest;self.lock=threading.RLock();self.cv=threading.Condition(self.lock)
        self.directories=[];self.files={};self.closed=False;self.closing=False;self.handles=[];self.streams=[];self.views=[];self.opened_names=False
        self.expected_names={};self.paths={};self.names_locked=False;self.native_files=None;self.native_entries=[]
        self.reader_capacity=2
        self.readers=queue.Queue(maxsize=self.reader_capacity);self.active=0;self.borrowed=0;self.peak_borrowed=0;self.completed=0
        self.k=c.WinDLL('kernel32',use_last_error=True)
        bindings={'CreateFileW':([w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE],w.HANDLE),'CloseHandle':([w.HANDLE],w.BOOL),
            'GetFileInformationByHandle':([w.HANDLE,c.POINTER(Info)],w.BOOL),'GetHandleInformation':([w.HANDLE,c.POINTER(w.DWORD)],w.BOOL),'GetFinalPathNameByHandleW':([w.HANDLE,w.LPWSTR,w.DWORD,w.DWORD],w.DWORD),
            'SetFilePointerEx':([w.HANDLE,c.c_longlong,c.POINTER(c.c_longlong),w.DWORD],w.BOOL),'ReadFile':([w.HANDLE,c.c_void_p,w.DWORD,c.POINTER(w.DWORD),c.c_void_p],w.BOOL)}
        for name,(args,ret) in bindings.items():f=getattr(self.k,name);f.argtypes=args;f.restype=ret
        from native_bindings import ShortCalls
        self.k=ShortCalls(self.k,'kernel32.dll',('GetFileInformationByHandle','GetFinalPathNameByHandleW'))
        try:
            for path in (*reversed(home.parents),home):
                h=self.open(path,0x81,3,True);self.named(h,path);self.directories.append((h,self.info(h,True),path))
            mh=self.open(home/'BUNDLE-MANIFEST.json',0x80000000,1,False);self.files['BUNDLE-MANIFEST.json']=(mh,self.info(mh,False),digest)
            raw=self.content(mh,16*1024*1024);b.require(hashlib.sha256(raw).hexdigest()==digest,'pinned_manifest_digest')
            m=b.strict(raw);b.require(set(m)=={'schema_version','files'} and m['schema_version']==1,'pinned_manifest_shape')
            for name,pin in m['files'].items():
                b.require(type(name) is str and '/' not in name and '\\' not in name and name not in ('.','..'),'pinned_member_name')
                h=self.open(home/name,0x80000000,1,False);self.files[name]=(h,self.info(h,False),pin)
            self.paths={name:home/name for name in self.files}
            # Each CreateFile opens a distinct FileObject/cursor. DuplicateHandle
            # would share the original cursor and is deliberately not used.
            # The master pins and directory handles remain open throughout.
            for _ in range(self.reader_capacity):
                import msvcrt
                files={};views={}
                for name,(_,identity,_) in self.files.items():
                    h=self.open(home/name,0x80000000,1,False);self.named(h,home/name)
                    b.require(self.info(h,False)==identity,'source_reader_identity_changed');files[name]=h
                    if ((identity[4]<<32)|identity[5])>0:
                        fd=msvcrt.open_osfhandle(h,os.O_RDONLY|os.O_BINARY|os.O_NOINHERIT);self.handles.remove(h)
                        try:stream=os.fdopen(fd,'rb',buffering=0)
                        except BaseException:os.close(fd);raise
                        self.streams.append(stream);view=mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ);self.views.append(view);views[name]=view
                self.readers.put({'files':files,'views':views,'data':c.create_string_buffer(65536),'name':c.create_unicode_buffer(32768)})
            self.verify()
            # Normalized names were checked before granting this mode. All
            # ancestors/files remain held without DELETE sharing, no reparse
            # or hardlinks, so their opened names cannot diverge by a rename.
            self.opened_names=True
            # Every file and ancestor is held without DELETE sharing and
            # every file has one link. The normalized name was checked on
            # each independent open above. A rename/replacement is therefore
            # prevented for the entire lifetime, not accepted from a saved
            # observation. Fresh ID/link/reparse/size/time and full-byte SHA
            # checks still run before/after every boundary. Ancestor names
            # continue to be queried on both sides of each complete verify.
            self.deny_probe()
            self.names_locked=True
            # Native batching preserves pre/hash/post order and rehashes all
            # bytes, while releasing the GIL across the complete fixed query.
            # Initial normalized names, exact Python hashes and WRITE/DELETE
            # refusal controls above must pass before this path is installed.
            import native_files
            self.native_files=native_files.Library()
            self.native_parameters=tuple(self.files.items());self.native_directory_parameters=tuple(self.directories)
            self.native_directories=native_files.Entries([(h,identity,path,None,None) for h,identity,path in self.directories]);self.native_entries.append(self.native_directories)
            readers=[]
            while not self.readers.empty():readers.append(self.readers.get_nowait())
            for reader in readers:
                b.require(set(reader['views'])==set(self.files),'native_source_all_mappings_required')
                reader['native_sources']=native_files.Entries([(reader['files'][name],identity,None,reader['views'][name],digest) for name,(_,identity,digest) in self.files.items()])
                self.native_entries.append(reader['native_sources']);self.readers.put(reader)
            self.verify()
        except BaseException:self.close();raise
    def open(self,path,access,sharing,directory):
        h=self.k.CreateFileW(str(path),access,sharing,None,3,0x00200000|(0x02000000 if directory else 0),None)
        b.require(h not in (None,c.c_void_p(-1).value),'source_pin_open_refused')
        try:
            flags=w.DWORD();b.require(self.k.GetHandleInformation(h,c.byref(flags)) and not flags.value&1,'source_handle_inheritance')
            self.info(h,directory);self.handles.append(h);return h
        except BaseException:self.k.CloseHandle(h);raise
    def info(self,h,directory):
        v=Info();b.require(self.k.GetFileInformationByHandle(h,c.byref(v)),'source_handle_information');return facts(v,directory)
    def named(self,h,path,buf=None):
        if buf is None:buf=c.create_unicode_buffer(32768)
        n=self.k.GetFinalPathNameByHandleW(h,buf,len(buf),8 if getattr(self,'opened_names',False) else 0)
        expected=self.expected_names.get(path)
        if expected is None:expected=('\\\\?\\'+str(path)).casefold();self.expected_names[path]=expected
        b.require(0<n<len(buf) and buf.value.casefold()==expected,'source_handle_named_path_changed')
    def content(self,h,limit,buf=None):
        b.require(self.k.SetFilePointerEx(h,0,None,0),'source_handle_seek');parts=[];total=0
        if buf is None:buf=c.create_string_buffer(65536)
        while True:
            n=w.DWORD();b.require(self.k.ReadFile(h,buf,len(buf),c.byref(n),None),'source_handle_read')
            if not n.value:break
            total+=n.value;b.require(total<=limit,'source_handle_read_budget');parts.append(c.string_at(buf,n.value))
        return b''.join(parts)
    def verify(self):
        with admission.locked(self.lock):
            b.require(not self.closed and not self.closing,'source_pin_already_closed');self.active+=1
        reader=None;counted=False;success=False
        try:
            wait=admission.live()
            try:reader=self.readers.get() if wait is None else self.readers.get(timeout=wait)
            except queue.Empty:raise admission.Expired('source_pin_reader_deadline') from None
            with self.lock:self.borrowed+=1;counted=True;self.peak_borrowed=max(self.peak_borrowed,self.borrowed)
            if getattr(self,'native_files',None) is not None:
                b.require(tuple(self.files.items())==self.native_parameters,'pinned_source_content_changed')
                b.require(tuple(self.directories)==self.native_directory_parameters,'source_ancestor_changed')
                self.native_files.check(self.native_directories,reader['name'])
                self.native_files.check(reader['native_sources'],reader['name'],hashes=True)
                self.native_files.check(self.native_directories,reader['name'])
                success=True;return self.digest
            for h,identity,path in self.directories:self.named(h,path,reader['name']);b.require(self.info(h,True)==identity,'source_ancestor_changed')
            for name,(_,identity,digest) in self.files.items():
                admission.live()
                h=reader['files'][name];path=self.paths[name] if getattr(self,'paths',None) else self.home/name
                if not getattr(self,'names_locked',False):self.named(h,path,reader['name'])
                b.require(self.info(h,False)==identity,'source_file_identity_changed')
                view=reader.get('views',{}).get(name)
                if view is None:actual=hashlib.sha256(self.content(h,256*1024*1024,reader['data'])).hexdigest()
                else:
                    # Full current-file SHA, with no validated byte/digest
                    # cache and no seek cursor shared between readers.
                    with memoryview(view) as raw:
                        b.require(raw.readonly,'source_mapping_not_readonly')
                        actual=hashlib.sha256(raw).hexdigest()
                b.require(actual==digest,'pinned_source_content_changed')
                b.require(self.info(h,False)==identity,'source_file_changed_during_read')
                if not getattr(self,'names_locked',False):self.named(h,path,reader['name'])
            for h,identity,path in self.directories:self.named(h,path,reader['name']);b.require(self.info(h,True)==identity,'source_ancestor_changed_after_read')
            admission.live()
            success=True;return self.digest
        finally:
            if reader is not None:self.readers.put(reader)
            with self.lock:
                if counted:self.borrowed-=1
                self.active-=1
                if success:self.completed+=1
                self.cv.notify_all()
    def statistics(self):
        with self.lock:return {'reader_capacity':getattr(self,'reader_capacity',2),'borrowed':self.borrowed,'active':self.active,'peak_borrowed':self.peak_borrowed,'completed':self.completed,'source_byte_cache':False,'independent_CreateFile_cursors':True,'source_read_representation':'readonly file mappings; every byte rehashed each boundary','opened_names_after_initial_normalized_check':getattr(self,'opened_names',False),'file_name_binding_enforced_by_nondeletable_single_link_handles':getattr(self,'names_locked',False),'ancestor_names_and_all_file_metadata_still_fresh':True}
    def deny_probe(self):
        rows=[]
        attempts=[(self.home/name,access) for name in self.files for access in (0x40000000,0x10000)]+[(self.home,0x10000)]
        for path,access in attempts:
            c.set_last_error(0);h=self.k.CreateFileW(str(path),access,7,None,3,0x00200000|(0x02000000 if path==self.home else 0),None);error=c.get_last_error()
            if h not in (None,c.c_void_p(-1).value):
                self.k.CloseHandle(h);raise b.Stop('pinned_'+('directory' if path==self.home else 'source')+('_DELETE' if access==0x10000 else '_WRITE')+'_open_succeeded')
            b.require(error==32,'source_pin_expected_sharing_violation');rows.append({'object':'directory' if path==self.home else path.name,'access':'DELETE' if access==0x10000 else 'WRITE','winerror':error})
        return rows
    def close(self):
        with self.lock:
            if self.closed:return
            self.closing=True;until=time.monotonic()+5
            while self.active:
                remain=until-time.monotonic();b.require(remain>0,'source_readers_not_drained');self.cv.wait(remain)
            okay=True
            if getattr(self,'native_files',None) is not None:
                self.native_files.close()
                for entry in self.native_entries:entry.close()
            for view in getattr(self,'views',()):
                try:view.close()
                except (OSError,BufferError):okay=False
            for stream in getattr(self,'streams',()):
                try:stream.close()
                except OSError:okay=False
            for h in reversed(self.handles):okay=bool(self.k.CloseHandle(h)) and okay
            self.closed=True;b.require(okay,'source_pin_handle_cleanup_failed')
