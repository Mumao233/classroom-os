"""Explicit-connection Alembic environment; no implicit local-app config access."""

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config


def _configure(connection):
    context.configure(connection=connection, target_metadata=None,
                      transaction_per_migration=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=config.get_main_option("sqlalchemy.url"),
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    supplied = config.attributes.get("connection")
    if supplied is not None:
        _configure(supplied)
    else:
        # URL may be provided by a test Config or an explicit Web-only env var.
        configured_url = config.get_main_option("sqlalchemy.url")
        if not configured_url or configured_url == "postgresql+psycopg://invalid/explicit_configuration_required":
            explicit_url = os.environ.get("CLASSROOM_WEB_DATABASE_URL")
            if explicit_url:
                config.set_main_option("sqlalchemy.url", explicit_url.replace("%", "%%"))
        engine = engine_from_config(config.get_section(config.config_ini_section),
                                    prefix="sqlalchemy.", poolclass=pool.NullPool)
        try:
            with engine.connect() as connection:
                _configure(connection)
        finally:
            engine.dispose()
