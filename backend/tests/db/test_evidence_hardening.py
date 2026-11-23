"""Step 2 hardening (migration 0003): restore boundary, digest integrity, retention window.

What the database does and does not guarantee, each shown with real LOGIN roles:
- A login holding only ingest_writer cannot call the restore function at all.
- The restore function cannot check that a row matches its keyed digest (no key in the
  database). A trusted evidence_restorer login calling it directly with consistent
  metadata and forged content IS able to store it; the reader then refuses that row.
  The same is true of a plain INSERT by ingest_writer. Integrity is enforced in Python
  before the write and detected on every read; it is not enforced by SQL.
- Retention: no row can be stored or restored already past its retention date, none can
  be held more than 180 days after its fetch, and a restore records the restoring fetch.
All rows are SYNTHETIC.
"""

import datetime as dt
import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.storage.evidence_pg import (
    EvidenceIntegrityError,
    EvidenceRetentionError,
    EvidenceWriteError,
    PostgresEvidenceReader,
    write_raw_rows,
)
from tests.db.test_evidence import (
    INSERT_SQL,
    KEY,
    NOW,
    SENTINEL,
    age_raw_rows,
    denied,
    expire,
    insert_params,
    item,
    scalar,
    write,
)

pytestmark = pytest.mark.db

FORGED = {"violationid": "1", "apartment": "FORGED-UNIT", "bbl": "2027540005"}
RESTORE_SQL = (
    "select evidence.restore_expired_raw_row(:d, 'wvxf-dwi5', '1', 'original_row', :rs, :s, "
    "cast(:r as jsonb), :f, 'u', null, :u)"
)


EXPIRED_AT = NOW - dt.timedelta(days=15)


def expired_item_1(db):
    """Item 1 fetched 20 days ago with a one-day retention and expired 15 days ago: a
    tombstone. A copy fetched after EXPIRED_AT may restore it."""
    write(db["writer"], [item(1)], retention_days=1)
    age_raw_rows(db, 20)
    assert expire(db, EXPIRED_AT) == 1
    return item(1)


def restore_params(it, row, fetched=None, retain_until=None):
    fetched = fetched or NOW - dt.timedelta(minutes=5)
    return {
        "d": it.original_digest,
        "rs": it.redacted_sha256,  # matches the tombstone
        "s": it.original_sha256,  # caller-supplied; the tombstone has none to compare
        "r": json.dumps(row),
        "f": fetched,
        "u": retain_until or fetched + dt.timedelta(days=180),
    }


def row_state(db, digest):
    with db["owner"].connect() as conn:
        return (
            conn.execute(
                text(
                    "select original_row->>'apartment' as apartment, fetched_at, retain_until, "
                    "expired_at, restore_count from evidence.raw_row where original_digest = :d"
                ),
                {"d": digest},
            )
            .mappings()
            .one()
        )


# ---- restore boundary and digest integrity ---------------------------------------------


def test_ingest_writer_alone_cannot_call_restore(db):
    it = expired_item_1(db)
    assert denied(db["ingest_writer"], RESTORE_SQL, **restore_params(it, FORGED))
    assert row_state(db, it.original_digest)["expired_at"] is not None  # still a tombstone


def test_restore_privilege_is_held_only_by_evidence_restorer(db):
    holders = {
        role
        for role in (
            "app_reader",
            "ingest_writer",
            "evidence_admin",
            "evidence_retention",
            "evidence_restorer",
        )
        if scalar(
            db["owner"],
            "select has_function_privilege(:r, p.oid, 'EXECUTE') from pg_proc p "
            "where proname = 'restore_expired_raw_row'",
            r=role,
        )
    }
    assert holders == {"evidence_restorer"}
    # The restorer role has nothing else: no table rights on raw evidence.
    for p in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert not scalar(
            db["owner"],
            "select has_table_privilege('evidence_restorer', 'evidence.raw_row', :p)",
            p=p,
        )
    assert denied(db["evidence_restorer"], "select original_row from evidence.raw_row")


def test_sql_function_does_not_verify_digest_integrity(db):
    """A direct call with matching metadata and forged content is stored: SQL cannot tell."""
    it = expired_item_1(db)
    with db["evidence_restorer"].begin() as conn:
        conn.execute(text(RESTORE_SQL), restore_params(it, FORGED))
    assert row_state(db, it.original_digest)["apartment"] == "FORGED-UNIT"
    # Detection happens where the key is: the reader refuses the row.
    with pytest.raises(EvidenceIntegrityError):
        PostgresEvidenceReader(db["evidence_admin"], KEY).get(it.original_digest)


def test_forged_plain_insert_is_likewise_only_detected_on_read(db):
    it = item(1)
    p = {**insert_params(it), "r": json.dumps(FORGED)}
    with db["ingest_writer"].begin() as conn:
        conn.execute(text(INSERT_SQL.format(tail="")), p)
    with pytest.raises(EvidenceIntegrityError):
        PostgresEvidenceReader(db["evidence_admin"], KEY).get(it.original_digest)


def test_python_writer_refuses_forged_rows_before_reaching_sql(db):
    it = expired_item_1(db)
    forged = it.model_copy(update={"original_row": FORGED})
    with pytest.raises(EvidenceIntegrityError):
        write(db["writer"], [forged])
    assert row_state(db, it.original_digest)["expired_at"] is not None


def test_writer_without_restorer_role_cannot_restore_through_python(db):
    it = expired_item_1(db)
    with pytest.raises(EvidenceWriteError, match="InsufficientPrivilege"):
        write(db["ingest_writer"], [it])


# ---- retention window ------------------------------------------------------------------


def test_restore_with_old_source_timestamp_is_refused(db):
    """Restoring a copy fetched more than 180 days ago would be due at once: refused."""
    it = expired_item_1(db)
    old = it.model_copy(update={"fetched_at": (NOW - dt.timedelta(days=200)).isoformat()})
    with pytest.raises(EvidenceRetentionError, match="past its 180-day retention date"):
        write(db["writer"], [old], retention_days=180)
    # The database enforces the same rule on a direct call: a copy fetched before the
    # expiry (migration 0010) ...
    stale = restore_params(it, it.original_row, fetched=NOW - dt.timedelta(days=200))
    with pytest.raises(DBAPIError, match="must be fetched after it expired"):
        with db["evidence_restorer"].begin() as conn:
            conn.execute(text(RESTORE_SQL), stale)
    # ... and a retention date already in the past (migration 0003).
    past = restore_params(it, it.original_row, retain_until=NOW - dt.timedelta(minutes=1))
    with pytest.raises(DBAPIError, match="already past its retention date"):
        with db["evidence_restorer"].begin() as conn:
            conn.execute(text(RESTORE_SQL), past)
    assert row_state(db, it.original_digest)["expired_at"] is not None


def test_restore_records_the_restoring_fetch_and_a_full_window(db):
    it = expired_item_1(db)
    fetched = NOW - dt.timedelta(days=10)
    newer = it.model_copy(update={"fetched_at": fetched.isoformat()})
    report = write(db["writer"], [newer], retention_days=180)
    assert report.restored == 1
    state = row_state(db, it.original_digest)
    # retain_until = fetched_at + 180 days, both from the restoring fetch.
    assert state["fetched_at"] == fetched
    assert state["retain_until"] == fetched + dt.timedelta(days=180)
    assert state["expired_at"] is None and state["restore_count"] == 1
    assert expire(db, NOW) == 0  # not due
    age_raw_rows(db, 180)  # 180 days on, the restored copy's window ends at `fetched`
    assert expire(db, fetched - dt.timedelta(seconds=1)) == 0
    assert expire(db, fetched) == 1  # due exactly at the window end


def test_retention_longer_than_180_days_is_refused(db):
    with pytest.raises(EvidenceRetentionError):
        write(db["writer"], [item(1)], retention_days=181)
    p = {**insert_params(item(1)), "u": NOW + dt.timedelta(days=181)}
    with pytest.raises(DBAPIError, match="retention_at_most_180_days"):
        with db["ingest_writer"].begin() as conn:
            conn.execute(text(INSERT_SQL.format(tail="")), p)
    it = expired_item_1(db)
    long = restore_params(it, it.original_row, retain_until=NOW + dt.timedelta(days=400))
    with pytest.raises(DBAPIError, match="retention_at_most_180_days"):
        with db["evidence_restorer"].begin() as conn:
            conn.execute(text(RESTORE_SQL), long)
    with pytest.raises(ValueError):
        Settings(_env_file=None, evidence_retention_days=181)


def test_plain_insert_already_past_retention_is_refused(db):
    old = item(1).model_copy(update={"fetched_at": (NOW - dt.timedelta(days=181)).isoformat()})
    with pytest.raises(EvidenceRetentionError):
        write(db["writer"], [old])
    p = {
        **insert_params(item(1)),
        "f": NOW - dt.timedelta(days=181),
        "u": NOW - dt.timedelta(days=1),
    }
    with pytest.raises(DBAPIError, match="already past its retention date"):
        with db["ingest_writer"].begin() as conn:
            conn.execute(text(INSERT_SQL.format(tail="")), p)
    assert scalar(db["owner"], "select count(*) from evidence.raw_row") == 0


def test_future_fetch_time_is_refused(db):
    p = {
        **insert_params(item(1)),
        "f": NOW + dt.timedelta(days=1),
        "u": NOW + dt.timedelta(days=100),
    }
    with pytest.raises(DBAPIError, match="fetch time is in the future"):
        with db["ingest_writer"].begin() as conn:
            conn.execute(text(INSERT_SQL.format(tail="")), p)


def test_retention_errors_carry_no_row_content(db):
    old = item(1).model_copy(update={"fetched_at": (NOW - dt.timedelta(days=181)).isoformat()})
    with pytest.raises(EvidenceRetentionError) as info:
        with db["writer"].begin() as conn:
            write_raw_rows(conn, [old], digest_key=KEY, retention_days=180)
    assert SENTINEL not in str(info.value)
