"""Deterministic synthetic ASR protocol POC; no external I/O."""
from .simulator import Simulator, ProtocolError

__all__ = ['Simulator', 'ProtocolError']
