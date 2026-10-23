"""Task 4 step 6: full-schema privacy audit with real logins and distinct sentinels.

One snapshot holds all five result types plus registration coverage, complaint groups,
the D9 report and link, the run, fetches and pages (tests/db/full_snapshot.py). Then:

- every column each non-admin login can SELECT, in every schema, is read as that login
  and walked recursively (JSON keys and values, arrays, scalars) for every sentinel kind
  and every private source key;
- app_reader's whole privilege surface is enumerated from the catalog: relations,
  columns, functions, schemas, role memberships, other sessions' queries;
- error messages (including chained exceptions and formatted tracebacks) and every log
  record, with SQLAlchemy's engine logger forced to DEBUG, are checked too;
- positive controls: the sentinels that should be held privately ARE in evidence.raw_row
  for evidence_admin, and corporate names, public roles, withheld markers and provenance
  stay public.
"""

# ruff: noqa: S608  (relation and column names come from the catalog; no input)

import datetime as dt
import logging
import traceback

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from nyc_apartment_agent.records.admin_models import ContactRole
from nyc_apartment_agent.storage.db import make_engine
from nyc_apartment_agent.storage.evidence_pg import (
    EvidenceRetentionError,
    EvidenceWriteError,
    PostgresEvidenceReader,
    write_raw_rows,
)
from nyc_apartment_agent.storage.repository import RecordWriteError, save_research_snapshot
from tests.db import full_snapshot as fs
from tests.db.conftest import url_for

pytestmark = pytest.mark.db

PRIVATE_KINDS = ("unit detail", "contact person", "business address", "311 identifier")
ROLES_WITHOUT_CONTENT = ("app_reader", "ingest_writer", "evidence_retention", "evidence_restorer")

_VISIBLE_COLUMNS = text(
    """
    SELECT n.nspname AS schema, c.relname AS relation, a.attname AS column
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
     WHERE c.relkind IN ('r', 'v', 'm', 'p', 'f')
       AND n.nspname NOT IN ('pg_catalog', 'information_schema')
       AND n.nspname NOT LIKE 'pg_toast%'
       AND has_schema_privilege(n.oid, 'USAGE')
       AND has_column_privilege(c.oid, a.attnum, 'SELECT')
     ORDER BY 1, 2, 3
    """
)


def walk(value, path=()):
    """Yield (path, string) for every key and string value in a JSON document."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield (*path, k), k
            yield from walk(v, (*path, k))
    elif isinstance(value, list):
        for v in value:
            yield from walk(v, path)
    elif value is not None:
        yield path, str(value)


def scan_as(engine) -> tuple[set, int, list]:
    """Read every visible column as this login. Returns relations, values seen, findings."""
    findings, seen, relations = [], 0, set()
    with engine.connect() as conn:
        columns = conn.execute(_VISIBLE_COLUMNS).all()
        for schema, relation, column in columns:
            relations.add(f"{schema}.{relation}")
            q = f'SELECT to_jsonb("{column}") FROM "{schema}"."{relation}"'
            for (doc,) in conn.execute(text(q)):
                for path, s in walk(doc):
                    seen += 1
                    where = f"{schema}.{relation}.{column}:{'.'.join(map(str, path))}"
                    for kind, values in fs.SENTINELS.items():
                        findings += [(kind, v, where) for v in values if v in s]
                    is_key = bool(path) and s == path[-1]
                    parent = path[-2] if len(path) > 1 else None
                    if is_key and s in fs.PRIVATE_KEYS and parent not in fs.FIELD_NAME_LISTS:
                        findings.append(("private key", s, where))
    return relations, seen, findings


@pytest.fixture
def saved(db):
    return fs.save(db["writer"], fs.build())


def test_no_login_without_content_rights_can_read_any_sentinel(db, saved, caplog):
    caplog.set_level(logging.DEBUG)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.DEBUG)
    try:
        loaded = fs.load_all(db["app_reader"], saved)
        reader = PostgresEvidenceReader(db["evidence_admin"], fs.KEY)
        for item in saved.buffer.items():
            assert reader.get(item.original_digest) is not None
        surfaces = {role: scan_as(db[role]) for role in ROLES_WITHOUT_CONTENT}
    finally:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.NOTSET)
    assert {t: s.result for t, s in loaded.items()} == saved.results

    reader_rel, reader_seen, reader_findings = surfaces["app_reader"]
    assert reader_findings == []
    assert reader_rel == {
        "records.retrieval_run",
        "records.source_fetch",
        "records.fetch_page",
        "records.normalized_record",
        "records.record_source_row",
        "records.complaint_group",
        "records.registration_coverage",
        "records.evidence_availability",
    }
    assert reader_seen > 2000  # every record, page, group, coverage row and run was read
    writer_rel, _, writer_findings = surfaces["ingest_writer"]
    assert writer_findings == []
    assert writer_rel == reader_rel | {"records.evidence_identity"}
    retention_rel, retention_seen, retention_findings = surfaces["evidence_retention"]
    assert retention_findings == [] and retention_rel == {"evidence.raw_row"}
    assert retention_seen == 2 * len(saved.buffer.items())  # digest + retain_until only
    assert surfaces["evidence_restorer"][:2] == (set(), 0)

    # Logs, with SQLAlchemy's engine logger at DEBUG while raw rows were read.
    assert caplog.records, "logging was captured"
    for s in fs.ALL_SENTINELS:
        assert s not in caplog.text, s


def test_private_values_are_held_only_in_raw_evidence(db, saved):
    """Positive control: the scan would find them, and they are where they belong."""
    with db["evidence_admin"].connect() as conn:
        raw = " ".join(
            conn.execute(text("select original_row::text from evidence.raw_row")).scalars()
        )
    for kind in PRIVATE_KINDS:
        for s in fs.SENTINELS[kind]:
            assert s in raw, (kind, s)
    # The typed address and credentials are stored nowhere, not even privately.
    with db["owner"].connect() as conn:
        everything = conn.execute(
            text(
                "select string_agg(to_jsonb(t)::text, ' ') from ("
                " select to_jsonb(r) as t from evidence.raw_row r"
                " union all select to_jsonb(x) from records.retrieval_run x"
                " union all select to_jsonb(x) from records.source_fetch x"
                " union all select to_jsonb(x) from records.fetch_page x"
                " union all select to_jsonb(x) from records.normalized_record x"
                " union all select to_jsonb(x) from records.record_source_row x"
                " union all select to_jsonb(x) from records.complaint_group x"
                " union all select to_jsonb(x) from records.registration_coverage x) q"
            )
        ).scalar()
    assert everything
    for kind in ("typed address", "credential"):
        for s in fs.SENTINELS[kind]:
            assert s not in everything, (kind, s)


def test_public_names_roles_markers_and_provenance_are_kept(db, saved):
    loaded = fs.load_all(db["app_reader"], saved)
    contacts = loaded["hpd_contacts"].result
    assert contacts.records[0].corporation_name == "SYNTHETIC OWNER LLC"
    assert {r.role for r in contacts.records} == {
        ContactRole.CORPORATE_OWNER,
        ContactRole.HEAD_OFFICER,
        ContactRole.AGENT,
    }
    assert all(r.withheld_fields for r in contacts.records)  # redacted-source indicators
    complaints = loaded["hpd_complaints"].result
    assert any(r.has_311_reference for r in complaints.records)  # a flag, not the number
    for stored in loaded.values():
        assert stored.result.provenance.pages  # query URLs, timestamps, attempts
        assert stored.evidence and all(e.held for e in stored.evidence.values())
    d9 = loaded["hpd_violations"]
    assert d9.buildings_fetch_id == saved.saved["hpd_buildings"].fetch_id


def test_app_reader_privilege_surface(db, saved):
    o = db["owner"]
    with o.connect() as conn:

        def q(sql, **p):
            return conn.execute(text(sql), p).all()

        # Relations: SELECT only, and only in records, never evidence_identity.
        rights = q(
            """
            select n.nspname || '.' || c.relname, p.priv
              from pg_class c join pg_namespace n on n.oid = c.relnamespace
              cross join unnest(array['SELECT','INSERT','UPDATE','DELETE','TRUNCATE',
                                      'REFERENCES','TRIGGER']) as p(priv)
             where n.nspname not in ('pg_catalog', 'information_schema')
               and n.nspname not like 'pg_toast%' and c.relkind in ('r','v','m','p','f','S')
               and has_table_privilege('app_reader', c.oid, p.priv)
            """
        )
        assert {priv for _, priv in rights} == {"SELECT"}
        relations = {rel for rel, _ in rights}
        assert all(r.startswith("records.") for r in relations)
        assert "records.evidence_identity" not in relations
        # Columns on any evidence relation: none.
        assert not q(
            """
            select 1 from pg_attribute a join pg_class c on c.oid = a.attrelid
              join pg_namespace n on n.oid = c.relnamespace
             where n.nspname = 'evidence' and a.attnum > 0
               and has_column_privilege('app_reader', c.oid, a.attnum, 'SELECT,INSERT,UPDATE')
            """
        )
        # Functions: app_reader can execute none of the project's functions.
        assert not q(
            """
            select p.oid::regprocedure from pg_proc p join pg_namespace n on n.oid = p.pronamespace
             where n.nspname in ('records', 'evidence', 'public')
               and has_function_privilege('app_reader', p.oid, 'EXECUTE')
            """
        )
        # The only SECURITY DEFINER function is the restore function, for the restorer.
        definers = q(
            """
            select p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace
             where n.nspname in ('records', 'evidence', 'public') and p.prosecdef
            """
        )
        assert [d[0] for d in definers] == ["restore_expired_raw_row"]
        # Schemas: usage on records only; create nowhere.
        for schema, usage in (("records", True), ("evidence", False), ("public", True)):
            assert q("select has_schema_privilege('app_reader', :s, 'USAGE')", s=schema) == [
                (usage,)
            ]
            assert q("select has_schema_privilege('app_reader', :s, 'CREATE')", s=schema) == [
                (False,)
            ]
        # Role memberships: app_reader belongs to no other role.
        assert not q(
            "select 1 from pg_auth_members m join pg_roles r on r.oid = m.member "
            "where r.rolname = 'app_reader'"
        )
    # Direct attempts, as a real app_reader login.
    r = db["app_reader"]
    for sql in (
        "select * from evidence.raw_row",
        "select original_row from evidence.raw_row",
        "select * from records.evidence_identity",
        "select evidence.restore_expired_raw_row('a','b','c','d','e','f','{}'::jsonb,"
        " now(), 'u', null, now())",
        "select records.text_array_distinct(array['1'])",
        "create table records.t (x int)",
        "create table public.t (x int)",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            with r.begin() as conn:
                conn.execute(text(sql))


def test_app_reader_cannot_see_other_sessions_queries(db, saved):
    writer = db["writer"].connect()
    tx = writer.begin()
    writer.execute(text("select count(*) from records.normalized_record"))
    try:
        with db["app_reader"].connect() as conn:
            rows = conn.execute(
                text(
                    "select query from pg_stat_activity "
                    "where usename = :u and pid <> pg_backend_pid()"
                ),
                {"u": db["writer"].url.username},
            ).scalars()
            assert set(rows) == {"<insufficient privilege>"}
    finally:
        tx.rollback()
        writer.close()


def _all_text(exc: BaseException) -> str:
    return "".join(traceback.format_exception(exc)) + repr(exc.__context__) + repr(exc.__cause__)


def test_error_messages_and_tracebacks_carry_no_private_value(db):
    snap = fs.build()
    failures = []
    # A D9 result without its building records: refused at commit.
    results = [r for t, r in snap.results.items() if t != "hpd_buildings"]
    with pytest.raises(RecordWriteError) as info:
        save_research_snapshot(
            db["writer"], snap.res, fs.RAW_INPUT, results, snap.buffer.items(), fs.SETTINGS
        )
    failures.append(info.value)
    items = sorted(snap.buffer.items(), key=lambda i: i.original_digest)
    unit = next(i for i in items if "SENTINEL-APT-9Z" in str(i.original_row))
    # A raw row past its retention date: refused in Python.
    old = unit.model_copy(
        update={"fetched_at": (dt.datetime.now(dt.UTC) - dt.timedelta(days=200)).isoformat()}
    )
    with pytest.raises(EvidenceRetentionError) as info:
        with db["writer"].begin() as conn:
            write_raw_rows(conn, [old], digest_key=fs.KEY, retention_days=180)
    failures.append(info.value)
    # A raw row the database refuses (fetch time in the future): scrubbed DB error.
    future = unit.model_copy(
        update={"fetched_at": (dt.datetime.now(dt.UTC) + dt.timedelta(days=1)).isoformat()}
    )
    with pytest.raises(EvidenceWriteError) as info:
        with db["writer"].begin() as conn:
            write_raw_rows(conn, [future], digest_key=fs.KEY, retention_days=180)
    failures.append(info.value)
    # A CHECK failure whose PostgreSQL DETAIL quotes the failing row.
    forged = unit.model_copy(update={"dataset_id": "NOT-A-DATASET"})
    with pytest.raises(EvidenceWriteError) as info:
        with db["writer"].begin() as conn:
            write_raw_rows(conn, [forged], digest_key=fs.KEY, retention_days=180)
    failures.append(info.value)
    for exc in failures:
        blob = _all_text(exc)
        for s in fs.ALL_SENTINELS:
            assert s not in blob, (type(exc).__name__, s)
        assert exc.__cause__ is None and exc.__context__ is None


def test_bound_values_never_reach_statement_text(db):
    """psycopg sends values separately from the SQL (server-side binding), so the statement
    text PostgreSQL sees, shows in pg_stat_activity and writes to its log on an error
    (log_min_error_statement) holds placeholders, not row content. Bound values reach the
    server log only if log_parameter_max_length_on_error is raised above its default 0."""
    a, b = db["writer"].connect(), db["writer"].connect()
    tx = a.begin()
    try:
        pid = a.execute(text("select pg_backend_pid()")).scalar()
        a.execute(text("select cast(:v as text) as v"), {"v": "SENTINEL-APT-9Z"})
        query = b.execute(
            text("select query from pg_stat_activity where pid = :p"), {"p": pid}
        ).scalar()
        assert "SENTINEL" not in query and "$1" in query
        assert b.execute(text("show log_parameter_max_length_on_error")).scalar() == "0"
    finally:
        tx.rollback()
        a.close()
        b.close()


def test_log_error_verbosity_can_be_made_terse_per_login(db, login_role, admin_engine):
    """Documented deployment setting (docs/operations/evidence_retention_runbook.md):
    terse drops DETAIL (which quotes failing rows), HINT, QUERY and CONTEXT from server
    log lines. It is superuser-only (or granted with GRANT SET), so it is set on the
    database or per login by the operator, not by the application. This proves the
    per-login form takes effect; it does not prove what any deployed server logs."""
    database = db["owner"].url.database
    user, password = login_role("ingest_writer", "evidence_restorer")
    with admin_engine.connect() as conn:
        conn.execute(
            text(f'ALTER ROLE {user} IN DATABASE "{database}" SET log_error_verbosity = terse')
        )
    engine = make_engine(url_for(database, user, password))
    try:
        with engine.connect() as conn:
            assert conn.execute(text("show log_error_verbosity")).scalar() == "terse"
            with pytest.raises(DBAPIError, match="permission denied"):
                conn.execute(text("set log_error_verbosity = verbose"))  # not the login's call
    finally:
        engine.dispose()
