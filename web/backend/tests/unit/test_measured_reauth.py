"""Capacity instrumentation preserves the reauth call and restores its timing context."""
import ast
from contextvars import ContextVar
from pathlib import Path

import pytest


def test_measured_auth_forwards_reauth_delegate_and_restores_timing_context():
    source = Path(__file__).resolve().parents[4] / 'tools/pr03b/product/capacity_service.py'
    # The capacity executable reads stdin and boots an SUT at module scope.
    # Run its actual subclass without running those top-level commands.
    tree = ast.parse(source.read_text(encoding='utf-8'))
    definition, = (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == 'MeasuredAuth')
    timer = ContextVar('test_capacity_operation', default='outer')
    missing = object()
    delegate = object()
    calls = []

    class AuthRecorder:
        def reauthenticate(self, ctx, body, *, admin_reauth=missing):
            calls.append((timer.get(), ctx, body, admin_reauth))
            if ctx == 'failed':
                raise ValueError('reauth failed')
            return 'ok'

    scope = {'AuthService': AuthRecorder, 'operation': timer}
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(source), 'exec'), scope)
    measured = scope['MeasuredAuth']()
    body = {'operation': {'candidate_parameters': {'idempotency_key': 'inner'}}}
    for options, expected in (({}, missing), ({'admin_reauth': None}, None), ({'admin_reauth': delegate}, delegate)):
        assert measured.reauthenticate('active', body, **options) == 'ok'
        assert calls[-1] == ('inner', 'active', body, expected)
        assert timer.get() == 'outer'
    with pytest.raises(ValueError, match='reauth failed'):
        measured.reauthenticate('failed', body, admin_reauth=None)
    assert calls[-1] == ('inner', 'failed', body, None)
    assert timer.get() == 'outer'
