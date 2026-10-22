"""Engine creation. Every engine hides SQL parameters from logs and error messages.

Parameters will carry raw source rows and digests, so `hide_parameters=True` keeps them
out of SQLAlchemy's statement logging and out of exception text. The URL itself can hold
a password: it is read from the environment and never logged.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine

from ..config import Settings, get_settings

# Group roles created by migration 0001 (NOLOGIN; deployments grant them to logins).
OWNER_ROLE = "nyc_owner"
APP_READER = "app_reader"
INGEST_WRITER = "ingest_writer"
EVIDENCE_ADMIN = "evidence_admin"
EVIDENCE_RETENTION = "evidence_retention"
ROLES = (OWNER_ROLE, APP_READER, INGEST_WRITER, EVIDENCE_ADMIN, EVIDENCE_RETENTION)
SCHEMAS = ("records", "evidence")


class DatabaseNotConfiguredError(RuntimeError):
    pass


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
