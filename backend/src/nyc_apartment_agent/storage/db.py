"""Engine creation. Every engine hides SQL parameters from logs and error messages.

Parameters will carry raw source rows and digests, so `hide_parameters=True` keeps them
out of SQLAlchemy's statement logging and out of exception text. The URL itself can hold
a password: it is read from the environment and never logged.

Result rows: when the `sqlalchemy.engine` logger is set to DEBUG, SQLAlchemy logs every
fetched row, and hide_parameters does not cover that, so an evidence_admin read would
put raw rows in the log (reproduced in step 6). A filter on the engine logger drops its
DEBUG records; statement logging at INFO still works, with parameters hidden.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, create_engine

from ..config import Settings, get_settings

# Group roles created by migrations 0001 and 0003 (NOLOGIN; deployments grant them to logins).
OWNER_ROLE = "nyc_owner"
APP_READER = "app_reader"
INGEST_WRITER = "ingest_writer"
EVIDENCE_ADMIN = "evidence_admin"
EVIDENCE_RETENTION = "evidence_retention"
EVIDENCE_RESTORER = "evidence_restorer"  # added by migration 0003
ROLES = (
    OWNER_ROLE,
    APP_READER,
    INGEST_WRITER,
    EVIDENCE_ADMIN,
    EVIDENCE_RETENTION,
    EVIDENCE_RESTORER,
)
SCHEMAS = ("records", "evidence")


class DatabaseNotConfiguredError(RuntimeError):
    pass


class _NoRowLogging(logging.Filter):
    """Drop SQLAlchemy's DEBUG engine records (result rows and column names)."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno > logging.DEBUG


ENGINE_LOGGER = logging.getLogger("sqlalchemy.engine.Engine")
if not any(isinstance(f, _NoRowLogging) for f in ENGINE_LOGGER.filters):
    ENGINE_LOGGER.addFilter(_NoRowLogging())


def sqlalchemy_url(url: str) -> str:
    """Use the psycopg 3 driver for plain postgresql:// URLs."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def make_engine(url: str | None = None, settings: Settings | None = None) -> Engine:
    if url is None:
        secret = (settings or get_settings()).database_url
        if secret is None:
            raise DatabaseNotConfiguredError("DATABASE_URL is not set")
        url = secret.get_secret_value()
    return create_engine(sqlalchemy_url(url), hide_parameters=True, echo=False, pool_pre_ping=True)
