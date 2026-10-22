"""PostgreSQL test fixtures. Each session uses disposable databases that it drops.

TEST_DATABASE_ADMIN_URL: a login that can CREATE DATABASE and CREATE ROLE (the CI
service's superuser, or a local cluster). Unset: database tests are skipped, so the
offline suite runs without PostgreSQL. With REQUIRE_DB_TESTS=1 (CI), a missing or
unreachable database fails instead, and any skipped database test fails the session.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url

from nyc_apartment_agent.storage.db import make_engine, sqlalchemy_url

BACKEND = Path(__file__).resolve().parents[2]
ADMIN_URL = os.getenv("TEST_DATABASE_ADMIN_URL")
REQUIRED = os.getenv("REQUIRE_DB_TESTS") == "1"
_skipped_db_tests: list[str] = []


def _unavailable(reason: str):
    if REQUIRED:
        pytest.fail(f"REQUIRE_DB_TESTS=1 but {reason}", pytrace=False)
    pytest.skip(reason)


def pytest_runtest_logreport(report):
    if report.skipped and "db" in report.keywords:
        _skipped_db_tests.append(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    if REQUIRED and _skipped_db_tests:
        session.exitstatus = 1
        print(f"\nREQUIRE_DB_TESTS=1 but {len(_skipped_db_tests)} database tests were skipped")


@pytest.fixture(scope="session")
def admin_engine() -> Iterator[Engine]:
    if not ADMIN_URL:
        _unavailable("TEST_DATABASE_ADMIN_URL is not set")
    engine = make_engine(ADMIN_URL)
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except Exception as exc:  # noqa: BLE001 - any connection failure means "unavailable"
        _unavailable(f"cannot connect to PostgreSQL ({type(exc).__name__})")
    yield engine.execution_options(isolation_level="AUTOCOMMIT")
    engine.dispose()


def url_for(database: str, user: str | None = None, password: str | None = None) -> str:
    url = make_url(sqlalchemy_url(ADMIN_URL)).set(database=database)
    if user is not None:
        url = url.set(username=user, password=password)
    return url.render_as_string(hide_password=False)


def alembic_config(engine_conn) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["connection"] = engine_conn
    cfg.attributes["configure_logger"] = False
    return cfg


def migrate(database: str, target: str = "head", down: bool = False) -> None:
    engine = make_engine(url_for(database))
    with engine.begin() as conn:
        cfg = alembic_config(conn)
        (command.downgrade if down else command.upgrade)(cfg, target)
    engine.dispose()


@pytest.fixture
def fresh_database(admin_engine) -> Iterator[str]:
    """An empty disposable database, dropped afterwards."""
    name = f"nyc_test_{secrets.token_hex(6)}"
    with admin_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield name
    finally:
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
def migrated_database(fresh_database) -> str:
    migrate(fresh_database)
    return fresh_database


@pytest.fixture
def login_role(admin_engine) -> Iterator:
    """Factory for real LOGIN users that are members of the given group roles. Dropped after."""
    made: list[str] = []

    def make(*groups: str) -> tuple[str, str]:
        user = f"t_{groups[0]}_{secrets.token_hex(4)}"
        password = secrets.token_urlsafe(16)
        with admin_engine.connect() as conn:
            conn.execute(
                text(f"CREATE ROLE {user} LOGIN PASSWORD '{password}' IN ROLE {', '.join(groups)}")
            )
        made.append(user)
        return user, password

    yield make
    with admin_engine.connect() as conn:
        for user in made:
            conn.execute(text(f"DROP ROLE IF EXISTS {user}"))


@pytest.fixture
def db(migrated_database, login_role):
    """Engines for one login per role on a migrated database, plus the admin engine."""
    engines = {"owner": make_engine(url_for(migrated_database))}
    for role in (
        "app_reader",
        "ingest_writer",
        "evidence_admin",
        "evidence_retention",
        "evidence_restorer",
    ):
        user, password = login_role(role)
        engines[role] = make_engine(url_for(migrated_database, user, password))
    # The server-side writer: inserts, and restores expired rows after verifying them.
    user, password = login_role("ingest_writer", "evidence_restorer")
    engines["writer"] = make_engine(url_for(migrated_database, user, password))
    yield engines
    for e in engines.values():
        e.dispose()
