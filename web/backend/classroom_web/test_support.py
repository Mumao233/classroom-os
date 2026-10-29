"""Pure, shared target guard for destructive synthetic-database test setup.

This module never opens a connection, reads PostgreSQL configuration files, or
creates a database. Both the CLI and pytest fixture must validate before any of
those actions. It is intentionally stricter than normal application DB config.
"""
from __future__ import annotations

from collections.abc import Mapping
import os
import re

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


class UnsafeTestDatabaseTarget(ValueError):
    """The supplied target or process environment can redirect libpq."""


SYNTHETIC_PASSWORD = "pr02a-synthetic-only-no-passfile"
_IDENTIFIER = re.compile(r"pr02a_[a-z0-9_]{1,56}\Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def validate_test_database_target(
    value: str, environ: Mapping[str, str] | None = None,
) -> URL:
    """Return an explicit safe target, or fail before engine/process creation.

    All libpq ``PG*`` settings are rejected, even empty ones. Query parameters
    are forbidden wholesale, including hostaddr/service/options and repetitions.
    Only literal IPv4 loopback is accepted: no DNS or Unix-socket resolution.
    A synthetic password replaces an absent password so libpq will not consult
    the user's default password file. Errors never echo credentials or values.
    """
    environment = os.environ if environ is None else environ
    if any(str(key).upper().startswith("PG") for key in environment):
        raise UnsafeTestDatabaseTarget("PostgreSQL tests forbid all inherited libpq PG* settings")
    if (not isinstance(value, str) or not value or len(value) > 4096
            or value.strip() != value or _CONTROL.search(value)):
        raise UnsafeTestDatabaseTarget("an explicit synthetic PostgreSQL URL is required")
    try:
        url = make_url(value)
    except (ArgumentError, TypeError, ValueError) as error:
        raise UnsafeTestDatabaseTarget("invalid synthetic PostgreSQL URL") from error
    if (url.drivername != "postgresql+psycopg"
            or url.host != "127.0.0.1"
            or type(url.port) is not int or not 1 <= url.port <= 65535 or url.port == 5432
            or not _IDENTIFIER.fullmatch(url.database or "")
            or not _IDENTIFIER.fullmatch(url.username or "")
            or url.query):
        raise UnsafeTestDatabaseTarget(
            "tests require postgresql+psycopg, literal 127.0.0.1, explicit non-5432 port, "
            "pr02a_ database/login identifiers, and no connection query parameters"
        )
    if url.password and (len(url.password) > 256 or _CONTROL.search(url.password)):
        raise UnsafeTestDatabaseTarget("invalid synthetic PostgreSQL credentials")
    return url.set(password=url.password or SYNTHETIC_PASSWORD)


def synthetic_connect_args() -> dict[str, str | int]:
    """Only code-owned connection options; callers cannot supply extra options."""
    return {"connect_timeout": 5, "sslmode": "disable"}
