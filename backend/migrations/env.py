"""Alembic environment. Online migrations only; SQL is written by hand (no ORM models).

The connection comes from the caller (tests pass `config.attributes["connection"]`) or
from DATABASE_URL. The URL is never written to the config or to logs.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from nyc_apartment_agent.storage.db import make_engine

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def run_migrations(connection) -> None:
    context.configure(connection=connection, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()


def main() -> None:
    if context.is_offline_mode():
        raise SystemExit("offline (SQL script) mode is not supported")
    connection = config.attributes.get("connection")
    if connection is not None:
        run_migrations(connection)
        return
    engine = make_engine()
    with engine.connect() as conn:
        run_migrations(conn)
        conn.commit()
    engine.dispose()


main()
