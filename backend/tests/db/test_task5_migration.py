"""Task 5 step 5: migration 0011 on disposable databases.

- base -> 0010 -> 0011 -> 0010 -> 0011 leaves every 0010 definition exactly as it was
  (constraints, functions, triggers, grants), so 0011 is additive and reversible;
- HPD snapshots saved under 0010 load unchanged after the upgrade and after a downgrade;
- the downgrade refuses while any Task 5 fetch, record or raw row exists, changes
  nothing when it refuses, and succeeds once they are gone.
"""

import pytest
from sqlalchemy import text

from nyc_apartment_agent.storage.db import make_engine
from tests.db import full_snapshot as fs
from tests.db import task5_recorded as t5
from tests.db.conftest import migrate, url_for
from tests.db.task5_recorded import save_recorded

pytestmark = pytest.mark.db

_DEFINITIONS = text(
    """
    select 'constraint', conrelid::regclass::text || '.' || conname, pg_get_constraintdef(oid)
      from pg_constraint where connamespace in ('records'::regnamespace, 'evidence'::regnamespace)
    union all
    select 'function', p.oid::regprocedure::text, pg_get_functiondef(p.oid)
      from pg_proc p where pronamespace in ('records'::regnamespace, 'evidence'::regnamespace)
    union all
    select 'trigger', tgrelid::regclass::text || '.' || tgname, pg_get_triggerdef(oid)
      from pg_trigger where not tgisinternal
    union all
    select 'grant', c.relnamespace::regnamespace::text || '.' || c.relname,
           coalesce(c.relacl::text, '')
      from pg_class c where relnamespace in ('records'::regnamespace, 'evidence'::regnamespace)
    union all
    select 'function grant', p.oid::regprocedure::text, coalesce(p.proacl::text, '')
      from pg_proc p where pronamespace in ('records'::regnamespace, 'evidence'::regnamespace)
    """
)


def definitions(database) -> set:
    engine = make_engine(url_for(database))
    with engine.connect() as conn:
        rows = set(map(tuple, conn.execute(_DEFINITIONS).all()))
    engine.dispose()
    return rows


def version(database) -> str:
    engine = make_engine(url_for(database))
    with engine.connect() as conn:
        v = conn.execute(text("select version_num from alembic_version")).scalar()
    engine.dispose()
    return v


@pytest.fixture
def engines(fresh_database, login_role):
    made = {}

    def get(role_groups=("ingest_writer", "evidence_restorer"), name="writer"):
        if name not in made:
            user, password = login_role(*role_groups)
            made[name] = make_engine(url_for(fresh_database, user, password))
        return made[name]

    yield get
    for e in made.values():
        e.dispose()


def test_0011_is_additive_and_reversible(fresh_database):
    migrate(fresh_database, "0010")
    at_0010 = definitions(fresh_database)
    migrate(fresh_database)
    assert version(fresh_database) == "0011"
    at_0011 = definitions(fresh_database)
    added = {d[:2] for d in at_0011} - {d[:2] for d in at_0010}
    assert ("trigger", "records.normalized_record.kind_task5") in added
    assert ("constraint", "evidence.raw_row.sr311_raw_row_token") in added
    # No new relation, so no new relation grant; no grant changed on an old relation.
    assert {d for d in at_0011 if d[0] == "grant"} == {d for d in at_0010 if d[0] == "grant"}
    migrate(fresh_database, "0010", down=True)
    assert definitions(fresh_database) == at_0010  # every 0010 definition restored exactly
    migrate(fresh_database)
    assert definitions(fresh_database) == at_0011


def test_hpd_snapshots_survive_the_upgrade_and_a_downgrade(fresh_database, engines):
    migrate(fresh_database, "0010")
    snap = fs.save(engines(), fs.build())
    reader = engines(("app_reader",), "reader")
    before = fs.load_all(reader, snap)
    assert {t: s.result for t, s in before.items()} == snap.results
    migrate(fresh_database)
    assert {t: s.result for t, s in fs.load_all(reader, snap).items()} == snap.results
    migrate(fresh_database, "0010", down=True)  # HPD rows do not block the downgrade
    assert {t: s.result for t, s in fs.load_all(reader, snap).items()} == snap.results
    migrate(fresh_database)
    _, saved = save_recorded(engines(), t5.build("west_17_220"))
    assert len(saved) == 3


def _counts(database) -> dict:
    engine = make_engine(url_for(database))
    with engine.connect() as conn:
        out = {
            t: conn.execute(text(f"select count(*) from {t}")).scalar()  # noqa: S608
            for t in (
                "records.source_fetch",
                "records.normalized_record",
                "records.record_source_row",
                "evidence.raw_row",
            )
        }
    engine.dispose()
    return out


def test_downgrade_refuses_while_task5_rows_exist(fresh_database, engines):
    migrate(fresh_database)
    save_recorded(engines(), t5.build("west_farms_1134"))
    before = _counts(fresh_database)
    with pytest.raises(Exception, match="Task 5 rows exist"):
        migrate(fresh_database, "0010", down=True)
    assert version(fresh_database) == "0011" and _counts(fresh_database) == before
    # Removing them is an operator decision outside the application roles.
    engine = make_engine(url_for(fresh_database))
    with engine.begin() as conn:
        conn.execute(text("delete from records.record_source_row"))
        conn.execute(text("delete from records.normalized_record"))
        conn.execute(text("delete from records.fetch_page"))
        conn.execute(text("delete from records.source_fetch"))
        conn.execute(text("delete from evidence.raw_row"))
    with engine.begin() as conn:  # a Task 5 raw row alone also blocks the downgrade
        conn.execute(
            text(
                "insert into evidence.raw_row (original_digest, dataset_id, "
                "source_record_id, basis, original_row, original_sha256, redacted_sha256, "
                "query_url, fetched_at, retain_until) values (repeat('b', 64), 'eabe-havv',"
                " 'row-x', 'original_row', '{}', repeat('b', 64), repeat('b', 64), 'u', "
                "now(), now() + interval '1 day')"
            )
        )
    with pytest.raises(Exception, match="Task 5 rows exist"):
        migrate(fresh_database, "0010", down=True)
    with engine.begin() as conn:
        conn.execute(text("delete from evidence.raw_row"))
    engine.dispose()
    migrate(fresh_database, "0010", down=True)
    assert version(fresh_database) == "0010"
    migrate(fresh_database)
    assert version(fresh_database) == "0011"
