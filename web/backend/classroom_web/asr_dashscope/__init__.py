"""Offline injection entrypoint for the PR-06a single-request protocol."""
from .adapter import DashScopeAdapter, Deadlines
from .billing import StreamIdentity, SyntheticRequestPermit
from .transport import FakeTransport, InboundText, Transport

__all__ = [
    'DashScopeAdapter', 'Deadlines', 'FakeTransport', 'InboundText', 'StreamIdentity',
    'SyntheticRequestPermit', 'Transport',
]
