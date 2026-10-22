"""Engine settings that need no database."""

import pytest

from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.storage.db import DatabaseNotConfiguredError, make_engine, sqlalchemy_url


def test_engine_hides_sql_parameters_and_never_echoes():
    engine = make_engine("postgresql://u:secret-pw@localhost:1/x")
    assert engine.hide_parameters is True and engine.echo is False
    assert engine.dialect.driver == "psycopg"
    assert "secret-pw" not in repr(engine) and "secret-pw" not in str(engine.url)


def test_plain_urls_use_psycopg3():
    assert sqlalchemy_url("postgres://h/db") == "postgresql+psycopg://h/db"
    assert sqlalchemy_url("postgresql+psycopg://h/db") == "postgresql+psycopg://h/db"


def test_missing_database_url_is_an_explicit_error():
    with pytest.raises(DatabaseNotConfiguredError):
        make_engine(settings=Settings(_env_file=None, database_url=None))


def test_blank_database_url_means_unset():
    assert Settings(_env_file=None, database_url="  ").database_url is None
