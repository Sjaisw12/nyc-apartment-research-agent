"""Least-privilege roles, tested by connecting as real LOGIN users (not just SET ROLE).

Step 1 has no tables yet, so each test creates probe objects as nyc_owner; they pick
up the default privileges exactly as the real tables in later steps will.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError

from nyc_apartment_agent.storage.db import make_engine
from tests.db.conftest import url_for

pytestmark = pytest.mark.db
DENIED = (ProgrammingError, OperationalError)


@pytest.fixture
def probes(migrated_database):
    engine = make_engine(url_for(migrated_database))
    with engine.begin() as conn:
        conn.execute(text("SET ROLE nyc_owner"))
        conn.execute(text("CREATE TABLE records.probe (id int primary key, note text)"))
        conn.execute(text("CREATE TABLE evidence.probe (id int primary key, secret text)"))
        conn.execute(text("INSERT INTO records.probe VALUES (1, 'public')"))
        conn.execute(text("INSERT INTO evidence.probe VALUES (1, 'SENTINEL-RAW-ROW')"))
        conn.execute(
            text("CREATE FUNCTION records.probe_fn() RETURNS int LANGUAGE sql AS 'select 1'")
        )
    engine.dispose()
    return migrated_database


def as_user(database, login_role, group):
    user, password = login_role(group)
    return make_engine(url_for(database, user, password))


def run(engine, sql):
    with engine.begin() as conn:
        return (
            conn.execute(text(sql)).all()
            if sql.lstrip().lower().startswith("select")
            else (conn.execute(text(sql)))
        )


def denied(engine, sql) -> bool:
    try:
        run(engine, sql)
    except DENIED as exc:
        assert "permission denied" in str(exc.orig).lower()
        return True
    return False


def test_app_reader_reads_records_and_cannot_reach_evidence(probes, login_role):
    e = as_user(probes, login_role, "app_reader")
    assert run(e, "select note from records.probe") == [("public",)]
    assert denied(e, "select * from evidence.probe")
    assert denied(e, "insert into records.probe values (2, 'x')")
    assert denied(e, "update records.probe set note = 'x'")
    assert denied(e, "delete from records.probe")
    assert denied(e, "create table records.mine (id int)")
    assert denied(e, "create table public.mine (id int)")
    e.dispose()


def test_app_reader_has_no_indirect_path_to_evidence(probes, login_role):
    e = as_user(probes, login_role, "app_reader")
    (row,) = run(
        e,
        "select has_schema_privilege('evidence', 'USAGE'), "
        "pg_has_role('evidence_admin', 'MEMBER'), pg_has_role('nyc_owner', 'MEMBER'), "
        "pg_has_role('ingest_writer', 'MEMBER'), pg_has_role('evidence_retention', 'MEMBER')",
    )
    assert row == (False, False, False, False, False)
    # Even resolving a name inside the schema is refused.
    assert denied(e, "select has_table_privilege('evidence.probe', 'SELECT')")
    # Functions: PUBLIC has no EXECUTE on functions the owner creates.
    assert denied(e, "select records.probe_fn()")
    e.dispose()


def test_no_security_definer_functions_and_no_public_execute(probes, admin_engine):
    engine = make_engine(url_for(probes))
    with engine.connect() as conn:
        definers = conn.execute(
            text(
                "select p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
                "where n.nspname in ('records', 'evidence') and p.prosecdef"
            )
        ).all()
        public_exec = conn.execute(
            text(
                "select p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
                "cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
                "where n.nspname in ('records', 'evidence') and a.grantee = 0"
            )
        ).all()
    engine.dispose()
    assert definers == [] and public_exec == []


def test_ingest_writer_inserts_but_never_updates_or_deletes(probes, login_role):
    e = as_user(probes, login_role, "ingest_writer")
    run(e, "insert into records.probe values (2, 'written')")
    assert len(run(e, "select * from records.probe")) == 2
    assert denied(e, "update records.probe set note = 'x'")
    assert denied(e, "delete from records.probe")
    # No default privilege on evidence tables: raw-row INSERT is granted per table later,
    # and reading raw rows back is never granted.
    assert denied(e, "select * from evidence.probe")
    e.dispose()


def test_evidence_admin_reads_evidence_only(probes, login_role):
    e = as_user(probes, login_role, "evidence_admin")
    assert run(e, "select secret from evidence.probe") == [("SENTINEL-RAW-ROW",)]
    assert denied(e, "insert into evidence.probe values (2, 'x')")
    assert denied(e, "select * from records.probe")
    e.dispose()


def test_evidence_retention_has_no_default_table_rights(probes, login_role):
    e = as_user(probes, login_role, "evidence_retention")
    assert denied(e, "select * from evidence.probe")  # granted on raw_row only, in step 2
    e.dispose()


def test_a_login_without_a_group_role_cannot_connect(probes, admin_engine):
    with admin_engine.connect() as conn:
        conn.execute(text("CREATE ROLE t_nobody_probe LOGIN PASSWORD 'pw-for-test-only'"))
    try:
        e = make_engine(url_for(probes, "t_nobody_probe", "pw-for-test-only"))
        with pytest.raises(OperationalError, match="permission denied"):
            with e.connect():
                pass
        e.dispose()
    finally:
        with admin_engine.connect() as conn:
            conn.execute(text("DROP ROLE t_nobody_probe"))
