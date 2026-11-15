"""Single-host, cross-process hash slots held until actual computation exits.

Deployments sharing a PG database must use this same trusted local slot file.
Multi-host authentication requires a separately validated coordinator; it is
not enabled by silently instantiating independent per-host counters.
"""
from contextlib import contextmanager,ExitStack
import os,time,tempfile
from pathlib import Path

from ..domain.auth import AuthRejected


def initialize_slots(path,count):
    """Publish only a fully initialized file; concurrent starters never see 0B."""
    if not path.exists():
        descriptor,name=tempfile.mkstemp(prefix='.slot-init-',dir=path.parent)
        temporary=Path(name)
        try:
            with os.fdopen(descriptor,'wb') as stream:stream.write(b'\0'*count);stream.flush()
            try:os.link(temporary,path)
            except FileExistsError:pass
        finally:
            if temporary.parent.resolve()!=path.parent.resolve():raise ValueError('slot temporary escaped its private directory')
            temporary.unlink()
    if path.is_symlink() or path.stat().st_size!=count:raise ValueError('invalid admission slot file')


class FileSlots:
    def __init__(self,path:Path,count:int):
        if os.name not in ('nt','posix'):raise ValueError('single-host Windows/Linux admission adapter required')
        self.path=path.resolve(strict=False)
        self.count=count
        if not self.path.is_absolute() or not self.path.parent.is_dir():raise ValueError('explicit existing private admission directory required')
        initialize_slots(self.path,count)

    @contextmanager
    def _claim(self,indices):
        indices=tuple(indices)
        if any(type(i) is not int or not 0<=i<self.count for i in indices):raise ValueError('slot outside configured set')
        held=None;file=self.path.open('r+b',buffering=0)
        try:
            for index in indices:
                try:
                    if os.name=='nt':
                        import msvcrt
                        file.seek(index)
                        msvcrt.locking(file.fileno(),msvcrt.LK_NBLCK,1)
                    else:
                        import fcntl
                        # flock is tied to this open file description, so it
                        # also excludes other threads in the same process.
                        # POSIX process-associated record locks would not.
                        slot=self.path.with_name(self.path.name+'-'+str(index))
                        initialize_slots(slot,1)
                        file.close();file=slot.open('r+b',buffering=0)
                        fcntl.flock(file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                    held=index;break
                except (OSError,BlockingIOError):continue
            yield held is not None
        finally:
            if held is not None:
                file.seek(held)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(file.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(file.fileno(),fcntl.LOCK_UN)
            file.close()

class HashAdmission(FileSlots):
    def __init__(self,path:Path,*,observer=None):
        super().__init__(path,10);self.observer=observer

    def __call__(self,body):
        entered=time.monotonic()
        def event(stage):
            if self.observer:self.observer(stage,time.monotonic()-entered)
        with self._claim(range(2)) as active:
            if active:
                if time.monotonic()>=entered+1:event('queue_timeout');raise AuthRejected('RATE_LIMITED',429)
                event('admitted');return body()
        with ExitStack() as waiting:
            queued=waiting.enter_context(self._claim(range(2,10)))
            if not queued:event('queue_full');raise AuthRejected('RATE_LIMITED',429)
            while time.monotonic()<entered+1:
                with self._claim(range(2)) as active:
                    if active:
                        waiting.close()
                        if time.monotonic()>=entered+1:event('queue_timeout');raise AuthRejected('RATE_LIMITED',429)
                        event('admitted')
                        return body()
                time.sleep(min(.005,max(0,entered+1-time.monotonic())))
        event('queue_timeout');raise AuthRejected('RATE_LIMITED',429)
