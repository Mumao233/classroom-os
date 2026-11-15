"""Explicit single-host WS admission, shared across API processes."""
from contextlib import contextmanager,ExitStack
from pathlib import Path
import os
from ..domain.auth import AuthRejected
from .auth_admission import FileSlots


class ByteSlots(FileSlots):
    def __init__(self,path,count):
        super().__init__(Path(path),count)

    @contextmanager
    def acquire(self):
        with self._claim(range(self.count)) as accepted:
            if not accepted:raise AuthRejected('RATE_LIMITED',429)
            yield


class WebSocketAdmission:
    def __init__(self,directory,credentials):
        self.directory=Path(directory).resolve()
        if not self.directory.is_dir() or self.directory.is_symlink():raise ValueError('explicit private WS admission directory required')
        self.crypto=credentials;self.total=ByteSlots(self.directory/'ws-total',64);self.pending=ByteSlots(self.directory/'ws-pending',16)

    @contextmanager
    def connection(self,source):
        # HMAC prevents disclosing source addresses through filenames.
        group=ByteSlots(self.directory/('ws-source-'+self.crypto.commitment('ws-source',source)),8)
        with ExitStack() as pending,self.total.acquire():
            pending.enter_context(self.pending.acquire());pending.enter_context(group.acquire())
            yield pending.close
