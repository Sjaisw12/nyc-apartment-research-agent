"""Migrations on a disposable PostgreSQL 16 database: up, down, up; ownership; grants."""

import pytest
from sqlalchemy import text

from nyc_apartment_agent.storage.db import ROLES, SCHEMAS, make_engine
from tests.db.conftest import migrate, url_for

pytestmark = pytest.mark.db


def q(database, sql, **params):
    engine = make_engine(url_for(database))
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).all()
    engine.dispose()
    return rows


def schemas(database):
    return {
        r[0]: r[1]
        for r in q(
            database,
            "select nspname, pg_get_userbyid(nspowner) from pg_namespace "
            "where nspname in ('records', 'evidence')",
        )
    }


def test_server_is_postgresql_16(fresh_database):
    (version,) = q(fresh_database, "show server_version_num")[0]
    assert int(version) // 10000 == 16


def test_upgrade_creates_owned_schemas_and_roles(migrated_database):
    assert schemas(migrated_database) == dict.fromkeys(SCHEMAS, "nyc_owner")
    rows = q(
        migrated_database,
        "select rolname, rolcanlogin from pg_roles where rolname = any(:r)",
        r=list(ROLES),
    )
    assert dict(rows) == dict.fromkeys(ROLES, False)  # group roles only, no logins
    (version,) = q(migrated_database, "select version_num from alembic_version")[0]
    assert version == "0010"


def test_up_down_up(fresh_database):
    migrate(fresh_database)
    migrate(fresh_database, "0002", down=True)  # 0003's restore boundary is undone
    granted = q(
        fresh_database,
        "select has_function_privilege('ingest_writer', p.oid, 'EXECUTE'), pronargs "
        "from pg_proc p where proname = 'restore_expired_raw_row'",
    )
    assert granted == [(True, 8)]
    migrate(fresh_database)
    migrate(fresh_database, "0001", down=True)  # one step back: the evidence table goes
    assert q(fresh_database, "select to_regclass('evidence.raw_row')")[0][0] is None
    assert schemas(fresh_database) == dict.fromkeys(SCHEMAS, "nyc_owner")
    migrate(fresh_database, "base", down=True)
    assert schemas(fresh_database) == {}
    # Roles are cluster-wide and deliberately kept on downgrade.
    kept = q(fresh_database, "select count(*) from pg_roles where rolname = any(:r)", r=list(ROLES))
    assert kept[0][0] == len(ROLES)
    assert q(fresh_database, "select count(*) from alembic_version")[0][0] == 0
    # PUBLIC gets its default database privileges back.
    acl = q(
        fresh_database, "select has_database_privilege('public', current_database(), 'CONNECT')"
    )
    assert acl[0][0] is True
    migrate(fresh_database)  # re-applies cleanly with the roles already present
    assert schemas(fresh_database) == dict.fromkeys(SCHEMAS, "nyc_owner")


def test_public_privileges_are_revoked(migrated_database):
    rows = q(
        migrated_database,
        "select has_database_privilege('public', current_database(), 'CONNECT'), "
        "has_database_privilege('public', current_database(), 'TEMPORARY'), "
        "has_schema_privilege('public', 'public', 'CREATE'), "
        "has_schema_privilege('public', 'records', 'USAGE'), "
        "has_schema_privilege('public', 'evidence', 'USAGE')",
    )
    assert rows[0] == (False, False, False, False, False)


def test_schema_grants(migrated_database):
    cases = {
        ("app_reader", "records"): True,
        ("app_reader", "evidence"): False,
        ("ingest_writer", "records"): True,
        ("ingest_writer", "evidence"): True,
        ("evidence_admin", "records"): False,
        ("evidence_admin", "evidence"): True,
        ("evidence_retention", "records"): False,
        ("evidence_retention", "evidence"): True,
        ("evidence_restorer", "records"): False,
        ("evidence_restorer", "evidence"): True,
    }
    for (role, schema), expected in cases.items():
        got = q(migrated_database, "select has_schema_privilege(:r, :s, 'USAGE')", r=role, s=schema)
        assert got[0][0] is expected, (role, schema)
    create = q(
        migrated_database,
        "select r from unnest(cast(:roles as text[])) r "
        "where has_schema_privilege(r, 'records', 'CREATE') "
        "or has_schema_privilege(r, 'evidence', 'CREATE')",
        roles=[r for r in ROLES if r != "nyc_owner"],
    )
    assert create == []  # only the owner can create objects


def test_default_privileges_are_recorded_for_the_owner(migrated_database):
    rows = q(
        migrated_database,
        "select coalesce(n.nspname, '*'), d.defaclobjtype, a.grantee::regrole::text, "
        "a.privilege_type from pg_default_acl d "
        "left join pg_namespace n on n.oid = d.defaclnamespace "
        "cross join lateral aclexplode(d.defaclacl) a "
        "where d.defaclrole = 'nyc_owner'::regrole and a.grantee <> 'nyc_owner'::regrole",
    )
    got = {(s, t, g, p) for s, t, g, p in rows}
    assert got == {
        ("records", "r", "app_reader", "SELECT"),
        ("records", "r", "ingest_writer", "SELECT"),
        ("records", "r", "ingest_writer", "INSERT"),
        ("evidence", "r", "evidence_admin", "SELECT"),
    }
    # The global entry removes PUBLIC's EXECUTE on new functions (grantee 0 is absent).
    funcs = q(
        migrated_database,
        "select count(*) from pg_default_acl d cross join lateral aclexplode(d.defaclacl) a "
        "where d.defaclrole = 'nyc_owner'::regrole and d.defaclobjtype = 'f' "
        "and a.grantee = 0",
    )
    assert funcs[0][0] == 0


def test_a_failing_migration_rolls_back_completely(fresh_database):
    # A pre-existing `evidence` schema makes 0001 fail part-way through.
    engine = make_engine(url_for(fresh_database))
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA evidence"))
    engine.dispose()
    with pytest.raises(Exception, match="already exists"):
        migrate(fresh_database)
    # Transactional DDL: `records` (created earlier in the same migration) is gone too,
    # and no version was recorded.
    assert schemas(fresh_database) == {"evidence": "postgres"}
    assert q(fresh_database, "select to_regclass('public.alembic_version')")[0][0] is None
