"""Bounded in-memory summaries plus complete nonsecret native JSONL evidence."""
import collections,hashlib,json,os,threading
class Events:
    def __init__(self,path):
        self.path=path;self.file=path.open('xb');self.hash=hashlib.sha256();self.count=0;self.kinds=collections.Counter();self.first=[];self.last=collections.deque(maxlen=8);self.lock=threading.RLock();self.parts=[];self.part_bytes=0;self.part_count=0;self.part_hash=hashlib.sha256()
    def close_part(self):
        if not self.file.closed:self.file.flush();os.fsync(self.file.fileno());self.file.close()
        self.parts.append({'file':self.file.name.rsplit('\\',1)[-1].rsplit('/',1)[-1],'bytes':self.part_bytes,'records':self.part_count,'sha256':self.part_hash.hexdigest()})
    def append(self,value):
        raw=(json.dumps(value,separators=(',',':'))+'\n').encode()
        with self.lock:
            if self.part_bytes and self.part_bytes+len(raw)>8*1024*1024:
                self.close_part();self.file=self.path.with_name(self.path.stem+'-part'+str(len(self.parts)).zfill(5)+self.path.suffix).open('xb');self.part_bytes=0;self.part_count=0;self.part_hash=hashlib.sha256()
            self.file.write(raw);self.hash.update(raw);self.count+=1;self.kinds[value.get('phase','unknown')]+=1
            self.part_bytes+=len(raw);self.part_count+=1;self.part_hash.update(raw)
            if len(self.first)<8:self.first.append(value)
            self.last.append(value)
    def finish(self):
        if not self.file.closed:self.close_part()
        return {'file':self.path.name,'parts':self.parts,'records':self.count,'sha256':self.hash.hexdigest(),'hash_scope':'all part bytes concatenated in index order','phase_counts':dict(self.kinds),'first':self.first,'last':list(self.last),'complete_records_retained':True}
