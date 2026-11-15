"""One ingress-based operation budget, propagated into actual worker threads."""
from contextvars import ContextVar
import time
from ..domain.auth import AuthRejected

DEADLINE=ContextVar('classroom_auth_absolute_deadline',default=None)


def remaining():
    deadline=DEADLINE.get()
    if deadline is None:return None
    value=deadline-time.monotonic()
    if value<=0:raise AuthRejected('RATE_LIMITED',429)
    return value
