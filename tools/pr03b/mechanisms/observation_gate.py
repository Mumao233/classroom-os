"""Parallel complete observations; backend creation/closure stays exclusive."""
from contextlib import contextmanager
import threading
import admission
class Gate:
    def __init__(self):
        self.cv=threading.Condition(threading.RLock());self.readers=0;self.writer=None;self.depth=0;self.waiters=0
    @contextmanager
    def shared(self):
        me=threading.get_ident();owned=False
        with self.cv:
            owned=self.writer==me
            if not owned:
                while self.writer is not None or self.waiters:
                    self.cv.wait(admission.live())
                admission.live();self.readers+=1
        try:yield
        finally:
            if not owned:
                with self.cv:self.readers-=1;self.cv.notify_all()
    def __enter__(self):
        me=threading.get_ident()
        with self.cv:
            if self.writer==me:self.depth+=1;return self
            self.waiters+=1
            try:
                while self.writer is not None or self.readers:self.cv.wait(admission.live())
                admission.live();self.writer=me;self.depth=1
            finally:self.waiters-=1;self.cv.notify_all()
        return self
    def __exit__(self,*exc):
        with self.cv:
            self.depth-=1
            if not self.depth:self.writer=None;self.cv.notify_all()
