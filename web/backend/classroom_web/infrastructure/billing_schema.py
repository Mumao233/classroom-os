"""Candidate schema asset, isolated from the shared Alembic revision chain."""
from pathlib import Path


def candidate_ddl() -> str:
    return Path(__file__).with_suffix('.sql').read_text(encoding='utf-8')


def integration_ddl() -> str:
    return Path(__file__).with_name('billing_integration.sql').read_text(encoding='utf-8')
