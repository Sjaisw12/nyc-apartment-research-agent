"""Task 4 step 2: private evidence table, availability view, atomic writes, expiry, restore.

Every permission test connects as a real LOGIN user holding exactly one group role.
All rows are SYNTHETIC; sentinel strings stand in for unit-level detail.
"""

import datetime as dt
import logging

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.records.evidence import verifies_original_row
from nyc_apartment_agent.records.hpd_violations import retrieve_hpd_violations
from nyc_apartment_agent.records.models import DigestBasis, RawEvidence
from nyc_apartment_agent.records.redaction import canonical_sha256, keyed_digest
from nyc_apartment_agent.storage.evidence_buffer import BufferedEvidenceStore
from nyc_apartment_agent.storage.evidence_pg import (
    DigestKeyNotConfiguredError,
    EvidenceIntegrityError,
    EvidenceWriteError,
    PostgresEvidenceReader,
    expire_due,
    save_evidence,
    write_raw_rows,
)
from tests.hpd_helpers import FakeSocrata, resolution, row

pytestmark = pytest.mark.db
KEY = b"synthetic-test-key"
SENTINEL = "SENTINEL-APT-9Z"
# Relative to the real clock: the database refuses rows already past their retention date.
NOW = dt.datetime.now(dt.UTC).replace(microsecond=0)
FETCHED = (NOW - dt.timedelta(hours=1)).isoformat()


def item(n: int = 1, basis=DigestBasis.ORIGINAL_ROW, **row_overrides) -> RawEvidence:
    raw = {"violationid": str(n), "apartment": SENTINEL, "bbl": "2027540005", **row_overrides}
    return RawEvidence(
        dataset_id="wvxf-dwi5",
        source_record_id=str(n),
        original_sha256=canonical_sha256(raw),
        original_digest=keyed_digest(raw, KEY),
        redacted_sha256=canonical_sha256({k: v for k, v in raw.items() if k != "apartment"}),
        redacted_fields=["apartment"],
        basis=basis,
        original_row=raw,
        query_url="https://data.cityofnewyork.us/resource/wvxf-dwi5.json?$where=bbl='2027540005'",
        fetched_at=FETCHED,
        data_as_of="Tue, 20 Oct 2026 14:55:26 GMT",
    )


def write(engine, items, retention_days=180):
    with engine.begin() as conn:
        return write_raw_rows(conn, items, digest_key=KEY, retention_days=retention_days)


def scalar(engine, sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


def denied(engine, sql, **params) -> bool:
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params)
    except DBAPIError as exc:
        return "permission denied" in str(exc.orig).lower()
    return False


# ---- 1. ownership and inherited grants ------------------------------------------------


def test_new_objects_are_owned_by_nyc_owner(db):
    owner = db["owner"]
    assert (
        scalar(
            owner,
            "select pg_get_userbyid(relowner) from pg_class "
            "where oid = 'evidence.raw_row'::regclass",
        )
        == "nyc_owner"
    )
    assert (
        scalar(
            owner,
            "select pg_get_userbyid(relowner) from pg_class "
            "where oid = 'records.evidence_availability'::regclass",
        )
        == "nyc_owner"
    )
    assert (
        scalar(
            owner,
            "select pg_get_userbyid(proowner) from pg_proc "
            "where proname = 'restore_expired_raw_row'",
        )
        == "nyc_owner"
    )
    # alembic_version stays with the migrating login (the role was reset before Alembic wrote it)
    assert (
        scalar(
            owner,
            "select pg_get_userbyid(relowner) from pg_class "
            "where oid = 'public.alembic_version'::regclass",
        )
        == "postgres"
    )


def test_table_privileges_by_role(db):
    expected = {
        ("app_reader", "records.evidence_availability"): {"SELECT"},
        ("ingest_writer", "records.evidence_availability"): {"SELECT"},
        ("evidence_admin", "records.evidence_availability"): set(),
        ("evidence_retention", "records.evidence_availability"): set(),
        ("app_reader", "evidence.raw_row"): set(),
        ("ingest_writer", "evidence.raw_row"): {"INSERT"},
        ("evidence_admin", "evidence.raw_row"): {"SELECT"},
        ("evidence_retention", "evidence.raw_row"): set(),  # column-level only
    }
    for (role, table), privs in expected.items():
        got = {
            p
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
            if scalar(db["owner"], "select has_table_privilege(:r, :t, :p)", r=role, t=table, p=p)
        }
        assert got == privs, (role, table)
    cols = {
        (c, p)
        for c in (
            "original_digest",
            "original_row",
            "original_sha256",
            "retain_until",
            "expired_at",
        )
        for p in ("SELECT", "UPDATE")
        if scalar(
            db["owner"],
            "select has_column_privilege('evidence_retention', 'evidence.raw_row', :c, :p)",
            c=c,
            p=p,
        )
    }
    assert cols == {
        ("original_digest", "SELECT"),
        ("retain_until", "SELECT"),
        ("expired_at", "SELECT"),
        ("original_row", "UPDATE"),
        ("original_sha256", "UPDATE"),
        ("expired_at", "UPDATE"),
    }


# ---- 2. exact SQL operations by login role ---------------------------------------------


INSERT_SQL = (
    "insert into evidence.raw_row (original_digest, dataset_id, source_record_id, basis, "
    "original_row, original_sha256, redacted_sha256, query_url, fetched_at, retain_until) "
    "values (:d, 'wvxf-dwi5', '1', 'original_row', cast(:r as jsonb), :s, :s, 'u', :f, :u) {tail}"
)


RESTORE_PROBE = (
    "select evidence.restore_expired_raw_row("
    "'a','b','c','d','e','f','{}'::jsonb, now(), 'u', null, now() + interval '1 day')"
)


def insert_params(it):
    import json

    return {
        "d": it.original_digest,
        "r": json.dumps(it.original_row),
        "s": it.original_sha256,
        "f": FETCHED,
        "u": (NOW + dt.timedelta(days=170)).isoformat(),
    }


def test_ingest_writer_insert_and_on_conflict(db):
    w, it = db["ingest_writer"], item()
    p = insert_params(it)
    with w.begin() as conn:
        conn.execute(text(INSERT_SQL.format(tail="on conflict do nothing")), p)
        conn.execute(text(INSERT_SQL.format(tail="on conflict do nothing")), p)
    assert scalar(db["owner"], "select count(*) from evidence.raw_row") == 1
    # A plain duplicate insert is a unique violation, not a silent overwrite.
    with pytest.raises(DBAPIError, match="duplicate key"):
        with w.begin() as conn:
            conn.execute(text(INSERT_SQL.format(tail="")), p)
    # Naming a conflict target needs SELECT on it: refused (the writer reads nothing).
    assert denied(w, INSERT_SQL.format(tail="on conflict (original_digest) do nothing"), **p)
    # DO UPDATE would need UPDATE (and SELECT): refused.
    assert denied(
        w,
        INSERT_SQL.format(tail="on conflict (original_digest) do update set data_as_of = 'x'"),
        **p,
    )
    # RETURNING would read rows back: refused.
    assert denied(
        w, INSERT_SQL.format(tail="on conflict do nothing returning original_digest"), **p
    )
    assert denied(w, "select original_row from evidence.raw_row")
    assert denied(w, "select original_digest from evidence.raw_row")
    assert denied(w, "update evidence.raw_row set data_as_of = 'x'")
    assert denied(w, "delete from evidence.raw_row")
    assert scalar(w, "select count(*) from records.evidence_availability") == 1
    assert denied(w, "insert into records.evidence_availability (original_digest) values ('x')")
    assert denied(w, "update records.evidence_availability set retain_until = now()")


def test_app_reader_sees_only_availability(db):
    write(db["ingest_writer"], [item()])
    r = db["app_reader"]
    with r.connect() as conn:
        res = conn.execute(text("select * from records.evidence_availability"))
        assert list(res.keys()) == [
            "original_digest",
            "basis",
            "held",
            "retain_until",
            "expired_at",
        ]
        (row,) = res.all()
    assert SENTINEL not in str(row) and row.held is True
    assert denied(r, "select * from evidence.raw_row")
    assert denied(r, RESTORE_PROBE)
    assert denied(r, "update records.evidence_availability set retain_until = now()")


def test_evidence_admin_reads_and_cannot_write_or_restore(db):
    write(db["ingest_writer"], [item()])
    a = db["evidence_admin"]
    assert scalar(a, "select original_row->>'apartment' from evidence.raw_row") == SENTINEL
    assert denied(a, "delete from evidence.raw_row")
    assert denied(a, RESTORE_PROBE)


def test_retention_role_expires_but_never_reads_content(db):
    write(db["ingest_writer"], [item()])
    t = db["evidence_retention"]
    assert denied(t, "select original_row from evidence.raw_row")
    assert denied(t, "select original_sha256 from evidence.raw_row")
    assert denied(t, "delete from evidence.raw_row")
    assert denied(t, "update evidence.raw_row set retain_until = now()")


# ---- 3. writer behaviour: idempotency, atomicity, integrity --------------------------


def test_write_is_idempotent(db):
    items = [item(1), item(2), item(3)]
    first = write(db["ingest_writer"], items)
    second = write(db["ingest_writer"], items)
    assert (first.inserted, first.already_held, first.restored) == (3, 0, 0)
    assert (second.inserted, second.already_held, second.restored) == (0, 3, 0)
    assert scalar(db["owner"], "select count(*) from evidence.raw_row") == 3


def test_failure_after_the_evidence_write_rolls_everything_back(db):
    w = db["ingest_writer"]
    with pytest.raises(DBAPIError, match="division by zero"):
        with w.begin() as conn:
            write_raw_rows(conn, [item(1), item(2)], digest_key=KEY, retention_days=180)
            conn.execute(text("select 1 / 0"))  # stands in for a failing public-record insert
    assert scalar(db["owner"], "select count(*) from evidence.raw_row") == 0
    with pytest.raises(RuntimeError, match="later step failed"):
        with w.begin() as conn:
            write_raw_rows(conn, [item(1)], digest_key=KEY, retention_days=180)
            raise RuntimeError("later step failed")
    assert scalar(db["owner"], "select count(*) from evidence.raw_row") == 0


def test_tampered_items_are_refused_before_anything_is_written(db):
    bad = item(2).model_copy(update={"original_row": {"violationid": "2", "apartment": "OTHER"}})
    with pytest.raises(EvidenceIntegrityError):
        write(db["ingest_writer"], [item(1), bad])
    wrong_key = item(3).model_copy(
        update={"original_digest": keyed_digest(item(3).original_row, b"other")}
    )
    with pytest.raises(EvidenceIntegrityError):
        write(db["ingest_writer"], [wrong_key])
    naive = item(4).model_copy(update={"fetched_at": "2026-10-22T01:00:00"})
    with pytest.raises(EvidenceIntegrityError, match="time zone"):
        write(db["ingest_writer"], [naive])
    assert scalar(db["owner"], "select count(*) from evidence.raw_row") == 0


def test_database_errors_are_scrubbed_of_row_content(db, caplog):
    caplog.set_level(logging.DEBUG)
    bad = item(5).model_copy(update={"dataset_id": "NOT A DATASET"})
    with pytest.raises(EvidenceWriteError) as info:
        write(db["ingest_writer"], [bad])
    assert "CheckViolation" in str(info.value)
    assert SENTINEL not in str(info.value) and info.value.__cause__ is None
    assert info.value.__suppress_context__
    assert SENTINEL not in caplog.text


def test_writes_require_a_configured_digest_key(db):
    with pytest.raises(DigestKeyNotConfiguredError):
        save_evidence(db["ingest_writer"], [item()], Settings(_env_file=None))
    settings = Settings(_env_file=None, evidence_digest_key=KEY.decode())
    assert save_evidence(db["ingest_writer"], [item()], settings).inserted == 1


# ---- 4. digest integrity and basis ----------------------------------------------------


def test_stored_rows_still_verify_and_keep_their_basis(db):
    original, sanitized = item(1), item(2, basis=DigestBasis.SANITIZED_ROW, apartment="[WITHHELD]")
    write(db["ingest_writer"], [original, sanitized])
    reader = PostgresEvidenceReader(db["evidence_admin"], KEY)
    got_o, got_s = reader.get(original.original_digest), reader.get(sanitized.original_digest)
    assert got_o.original_row == original.original_row and got_o.basis is DigestBasis.ORIGINAL_ROW
    assert verifies_original_row(got_o, KEY)
    assert got_s.basis is DigestBasis.SANITIZED_ROW and not verifies_original_row(got_s, KEY)
    assert (
        scalar(
            db["owner"],
            "select basis from records.evidence_availability where original_digest = :d",
            d=sanitized.original_digest,
        )
        == "sanitized_row"
    )


def test_buffered_adapter_evidence_saves_in_one_transaction(db):
    buffer = BufferedEvidenceStore()
    fake = FakeSocrata([row(1), row(2, apartment="4B"), row(3, bin=None)])
    with fake.client() as c:
        result = retrieve_hpd_violations(
            resolution(), c, evidence=buffer, digest_key=KEY, page_size=10
        )
    assert len(buffer) == len(result.records) == 3
    report = write(db["ingest_writer"], buffer.items())
    assert report.inserted == 3
    reader = PostgresEvidenceReader(db["evidence_admin"], KEY)
    for r in result.records:  # each public record's digest resolves to its stored row
        assert verifies_original_row(reader.get(r.original_digest), KEY)


# ---- 5. expiry, lineage and narrowly authorized restoration ---------------------------


def expire(db, now):
    with db["evidence_retention"].begin() as conn:
        return expire_due(conn, now)


def test_expiry_leaves_a_tombstone_and_keeps_lineage(db):
    write(db["ingest_writer"], [item(1)], retention_days=1)
    write(db["ingest_writer"], [item(2)], retention_days=180)
    assert expire(db, NOW) == 0  # nothing is due yet
    later = NOW + dt.timedelta(days=2)
    assert expire(db, later) == 1
    o = db["owner"]
    d1, d2 = item(1).original_digest, item(2).original_digest
    row1 = dict(
        db["owner"]
        .connect()
        .execute(
            text(
                "select original_row, original_sha256, expired_at, dataset_id, redacted_sha256 "
                "from evidence.raw_row where original_digest = :d"
            ),
            {"d": d1},
        )
        .mappings()
        .one()
    )
    assert row1["original_row"] is None and row1["original_sha256"] is None
    assert row1["expired_at"] == later and row1["dataset_id"] == "wvxf-dwi5"
    assert (
        scalar(o, "select held from records.evidence_availability where original_digest = :d", d=d1)
        is False
    )
    assert (
        scalar(o, "select held from records.evidence_availability where original_digest = :d", d=d2)
        is True
    )
    assert PostgresEvidenceReader(db["evidence_admin"], KEY).get(d1) is None
    assert expire(db, later) == 0  # idempotent


def test_reinserting_an_expired_row_restores_it_through_the_narrow_function(db):
    write(db["ingest_writer"], [item(1)], retention_days=1)
    expire(db, NOW + dt.timedelta(days=2))
    report = write(db["writer"], [item(1)], retention_days=180)
    assert (report.inserted, report.already_held, report.restored) == (0, 0, 1)
    reader = PostgresEvidenceReader(db["evidence_admin"], KEY)
    assert verifies_original_row(reader.get(item(1).original_digest), KEY)
    info = (
        db["owner"]
        .connect()
        .execute(
            text(
                "select restore_count, restored_at is not null as r, retain_until "
                "from evidence.raw_row"
            )
        )
        .one()
    )
    assert info.restore_count == 1 and info.r
    assert info.retain_until == dt.datetime.fromisoformat(FETCHED) + dt.timedelta(days=180)


def test_restore_refuses_mismatched_metadata_and_held_rows(db):
    write(db["ingest_writer"], [item(1)], retention_days=1)
    expire(db, NOW + dt.timedelta(days=2))
    # Same row and digest, but the metadata disagrees with the tombstone: refused, and
    # the whole write is rolled back rather than the new original being ignored.
    mismatched = item(1).model_copy(update={"source_record_id": "999"})
    with pytest.raises(EvidenceWriteError, match="P0002"):
        write(db["writer"], [mismatched, item(7)])
    assert scalar(db["owner"], "select count(*) from evidence.raw_row") == 1  # item 7 not kept
    assert scalar(db["owner"], "select expired_at is not null from evidence.raw_row") is True
    # Calling the function on held evidence never overwrites it.
    write(db["ingest_writer"], [item(2)])
    p = insert_params(item(2))
    with pytest.raises(DBAPIError, match="no expired evidence"):
        with db["writer"].begin() as conn:
            conn.execute(
                text(
                    "select evidence.restore_expired_raw_row(:d, 'wvxf-dwi5', '2', 'original_row', "
                    ":rs, :s, cast(:r as jsonb), now(), 'u', null, now() + interval '1 day')"
                ),
                {**p, "rs": item(2).redacted_sha256},
            )
