"""Bounded fresh file observations and complete SHA256 outside the Python GIL.

Only parameters (held handles, expected identities/digests and readonly buffer
addresses) persist. Results, bytes, authorization and OS observations do not.
"""
import ctypes as c,hashlib,time,threading
from ctypes import wintypes as w
from pathlib import Path
import admission,common as b

class Entry(c.Structure):
    _fields_=[('handle',w.HANDLE),('volume',w.DWORD),('index_hi',w.DWORD),('index_lo',w.DWORD),('attributes',w.DWORD),('size_hi',w.DWORD),('size_lo',w.DWORD),('links',w.DWORD),('written_hi',w.DWORD),('written_lo',w.DWORD),('directory',w.DWORD),('path',w.LPCWSTR),('data',c.c_void_p),('length',w.DWORD),('digest',c.c_ubyte*32),('path_after',w.DWORD)]
class Buffer(c.Structure):
    _fields_=[('buf',c.c_void_p),('obj',c.c_void_p),('length',c.c_ssize_t),('itemsize',c.c_ssize_t),('readonly',c.c_int),('ndim',c.c_int),('format',c.c_void_p),('shape',c.c_void_p),('strides',c.c_void_p),('suboffsets',c.c_void_p),('internal',c.c_void_p)]

class Entries:
    def __init__(self,rows):
        b.require(0<len(rows)<=256 and c.sizeof(Entry)==104 and c.sizeof(Buffer)==80,'native_file_ABI')
        self.array=(Entry*len(rows))();self.exports=[];self.closed=False
        # Private prototypes: changing pythonapi's shared function object's
        # argtypes would invalidate guard_io's separate Py_buffer structure.
        # PYFUNCTYPE retains the GIL required by both CPython APIs.
        get=c.PYFUNCTYPE(c.c_int,c.py_object,c.POINTER(Buffer),c.c_int)(('PyObject_GetBuffer',c.pythonapi))
        self.release=c.PYFUNCTYPE(None,c.POINTER(Buffer))(('PyBuffer_Release',c.pythonapi))
        try:
            for index,(handle,identity,path,view,digest) in enumerate(rows):
                item=Entry();item.handle=handle;item.volume,item.index_hi,item.index_lo=identity[:3];item.directory=len(identity)==3
                b.require(len(identity) in (3,9),'native_file_identity_shape')
                if not item.directory:item.attributes,item.size_hi,item.size_lo,item.links,item.written_hi,item.written_lo=identity[3:]
                if path is not None:
                    name='\\\\?\\'+str(path);b.require(name.isascii() and len(name)<32768,'native_file_fixed_ASCII_name');item.path=name
                if view is not None:
                    buf=Buffer();get(view,c.byref(buf),0);self.exports.append(buf)
                    b.require(buf.readonly==1 and buf.length==item.size_lo and not item.directory and path is None,'native_file_readonly_source')
                    item.data=buf.buf;item.length=buf.length;raw=bytes.fromhex(digest)
                    b.require(len(raw)==32,'native_file_SHA256_shape');item.digest[:]=raw
                self.array[index]=item
        except BaseException:self.close();raise
    def close(self):
        if self.closed:return
        for buf in reversed(self.exports):self.release(c.byref(buf))
        self.exports=[];self.closed=True

class Library:
    def __init__(self):
        root=Path(__file__).resolve().parent
        provenance=b.strict((root/'native_identity.build.json').read_bytes())
        for name,key in (('native_identity.dll','artifact_sha256'),('native_identity.c','source_sha256')):
            b.require(hashlib.sha256((root/name).read_bytes()).hexdigest()==provenance[key],'native_file_build_changed')
        self.dll=c.WinDLL(str(root/'native_identity.dll'),use_last_error=True)
        self.match=self.dll.pr03b_match_files;self.verify=self.dll.pr03b_verify_sources
        for function in (self.match,self.verify):
            function.argtypes=[c.POINTER(Entry),w.DWORD,w.LPWSTR,w.DWORD,c.c_ulonglong,c.POINTER(w.DWORD)];function.restype=w.BOOL
        self.kernel=c.WinDLL('kernel32',use_last_error=True);self.kernel.GetTickCount64.restype=c.c_ulonglong
        b.require(abs(self.kernel.GetTickCount64()/1000-time.monotonic())<.02,'native_file_deadline_clock')
        self.lock=threading.RLock();self.cv=threading.Condition(self.lock);self.active=0;self.closed=False;self.closing=False;self.calls=0
    def check(self,entries,scratch,*,hashes=False):
        b.require(not entries.closed,'native_file_entries_closed')
        with admission.locked(self.lock):
            b.require(not self.closed and not self.closing,'native_file_library_closed');self.active+=1
        try:
            deadline=admission.CURRENT.get();error=w.DWORD()
            okay=(self.verify if hashes else self.match)(entries.array,len(entries.array),scratch,len(scratch),0 if deadline is None else int(deadline*1000),c.byref(error))
            with self.lock:self.calls+=1
            if error.value==1460:raise admission.Expired('ingress_deadline')
            b.require(okay,'native_file_fresh_'+('SHA256' if hashes else 'metadata')+'_mismatch_'+str(error.value))
            admission.live()
        finally:
            with self.cv:self.active-=1;self.cv.notify_all()
    def close(self):
        with self.cv:
            if self.closed:return
            self.closing=True;until=time.monotonic()+5
            while self.active:
                remain=until-time.monotonic();b.require(remain>0,'native_file_queries_not_drained');self.cv.wait(remain)
            self.kernel.FreeLibrary.argtypes=[w.HMODULE];self.kernel.FreeLibrary.restype=w.BOOL
            b.require(self.kernel.FreeLibrary(self.dll._handle),'native_file_DLL_unload');self.closed=True
