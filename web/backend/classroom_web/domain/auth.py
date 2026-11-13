"""Authentication values and policy; never trust a browser-supplied identity."""
from dataclasses import dataclass,field
from datetime import timedelta
from typing import Protocol


class AuthRejected(Exception):
    def __init__(self, code='UNAUTHENTICATED', status=401):
        self.code,self.status=code,status
        super().__init__(code)


@dataclass(frozen=True)
class AuthPolicy:
    student_idle: timedelta=timedelta(days=14)
    student_absolute: timedelta=timedelta(days=30)
    admin_idle: timedelta=timedelta(minutes=15)
    admin_absolute: timedelta=timedelta(hours=8)
    challenge: timedelta=timedelta(minutes=5)
    recovery_flow: timedelta=timedelta(minutes=10)
    ws_ticket: timedelta=timedelta(seconds=30)
    ws_first_frame: float=5.0

    def __post_init__(self):
        if (self.student_idle!=timedelta(days=14) or self.student_absolute!=timedelta(days=30)
                or self.admin_idle!=timedelta(minutes=15) or self.admin_absolute!=timedelta(hours=8)
                # These three lifetimes are fixed inside the database functions. Accepting a
                # shorter value here would be silently ignored, so only the enforced ones pass.
                or self.challenge!=timedelta(minutes=5) or self.recovery_flow!=timedelta(minutes=10)
                or self.ws_ticket!=timedelta(seconds=30)
                or not 0<self.ws_first_frame<=5):
            raise ValueError('invalid authentication policy')


@dataclass(frozen=True)
class BrowserContext:
    session: str|None=field(default=None,repr=False)
    preauth: str|None=field(default=None,repr=False)
    recovery: str|None=field(default=None,repr=False)
    csrf: str|None=field(default=None,repr=False)
    reauth: str|None=field(default=None,repr=False)
    source: str=field(default='',repr=False)


@dataclass(frozen=True)
class AuthReply:
    body: dict=field(repr=False)
    session: str|None=field(default=None,repr=False)
    preauth: str|None=field(default=None,repr=False)
    recovery: str|None=field(default=None,repr=False)
    persistent_seconds: int|None=None
    clear_session: bool=False
    clear_recovery: bool=False


class AuthRepository(Protocol):
    def call(self, operation:str, payload:dict, *, verifier:bool=False)->dict: ...


class PasswordPolicy(Protocol):
    def validate(self,password:str,role:str): ...
    def current(self,approval): ...
