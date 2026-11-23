"""Task 4 step 6: raw-evidence lifecycle rules (migration 0010) and restoration.

Every test connects as real LOGIN users. Rows are written with the real clock (the
database refuses anything else) and moved into the past with `age_raw_rows`. All rows
are SYNTHETIC.
"""

import datetime as dt

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from nyc_apartment_agent.records.evidence import verifies_original_row
from nyc_apartment_agent.storage.evidence_pg import PostgresEvidenceReader
from tests.db.test_evidence import KEY, NOW, age_raw_rows, expire, item, scalar, write

pytestmark = pytest.mark.db

DAY = dt.timedelta(days=1)


def states(db) -> dict[str, dict]:
    """Every raw row's columns other than its content, keyed by source id (owner view)."""
    with db["owner"].connect() as conn:
        rows = conn.execute(
            text(
                "select source_record_id, original_row is not null as held_content, "
                "to_jsonb(r) - array['original_row', 'original_sha256', 'expired_at'] as meta, "
                "expired_at from evidence.raw_row r"
            )
        ).mappings()
        return {r["source_record_id"]: dict(r) for r in rows}


def attempt(engine, sql, **params):
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params)
    except DBAPIError as exc:
        return str(exc.orig).splitlines()[0]
    return "committed"


# ---- lifecycle rules (migration 0010), reproduced first as evidence_retention ----------


def test_retention_login_cannot_expire_early_overwrite_or_resurrect(db):
    write(db["writer"], [item(1), item(2)], retention_days=180)
    write(db["writer"], [item(3)], retention_days=1)
    age_raw_rows(db, 2)
    assert expire(db, NOW) == 1  # item 3 is due and becomes a tombstone
    before = states(db)
    t = db["evidence_retention"]
    d = {n: item(n).original_digest for n in (1, 2, 3)}
    # Expire a row that is not due, with the database's own clock.
    assert attempt(
        t,
        "update evidence.raw_row set original_row = null, original_sha256 = null, "
        "expired_at = now() where original_digest = :d",
        d=d[1],
    ) == ("raw evidence is not yet due for expiry")
    # Overwrite held evidence.
    assert attempt(
        t,
        'update evidence.raw_row set original_row = \'{"forged": "x"}\' where original_digest = :d',
        d=d[2],
    ) == ("held raw evidence cannot be changed")
    # Bring a tombstone back with invented content, around the restore function.
    assert attempt(
        t,
        'update evidence.raw_row set original_row = \'{"forged": "y"}\', '
        "original_sha256 = repeat('a', 64), expired_at = null where original_digest = :d",
        d=d[3],
    ) == ("expired evidence can only be restored by the restore function")
    # Rewrite a tombstone's expiry time.
    assert attempt(
        t,
        "update evidence.raw_row set expired_at = expired_at - interval '1 day' "
        "where original_digest = :d",
        d=d[3],
    ) == ("a raw evidence tombstone cannot be changed")
    # A future clock passed to the expiry statement is refused too.
    with pytest.raises(DBAPIError, match="not yet due for expiry"):
        expire(db, NOW + 400 * DAY)
    assert states(db) == before
    reader = PostgresEvidenceReader(db["evidence_admin"], KEY)
    assert verifies_original_row(reader.get(d[1]), KEY)
    assert verifies_original_row(reader.get(d[2]), KEY)


def test_expiry_cannot_set_a_time_before_the_retention_date(db):
    write(db["writer"], [item(1)], retention_days=1)
    age_raw_rows(db, 5)
    due = scalar(db["owner"], "select retain_until from evidence.raw_row")
    assert attempt(
        db["evidence_retention"],
        "update evidence.raw_row set original_row = null, original_sha256 = null, expired_at = :t",
        t=due - dt.timedelta(seconds=1),
    ) == ("expiry time must lie between the retention date and now")


# ---- restoration after expiry -------------------------------------------------------------


def test_newly_fetched_evidence_restores_a_tombstone_with_a_fresh_window(db):
    write(db["writer"], [item(1)], retention_days=1)
    age_raw_rows(db, 30)
    expired_at = NOW - 20 * DAY
    assert expire(db, expired_at) == 1
    fresh = item(1)  # fetched an hour ago: after the expiry
    report = write(db["writer"], [fresh], retention_days=180)
    assert (report.restored, report.not_restored) == (1, 0)
    with db["owner"].connect() as conn:
        row = conn.execute(
            text("select fetched_at, retain_until, restore_count from evidence.raw_row")
        ).one()
    assert row.fetched_at == dt.datetime.fromisoformat(fresh.fetched_at)
    assert row.retain_until == row.fetched_at + 180 * DAY and row.restore_count == 1
    reader = PostgresEvidenceReader(db["evidence_admin"], KEY)
    assert verifies_original_row(reader.get(fresh.original_digest), KEY)


def test_a_copy_fetched_before_the_expiry_does_not_restart_the_window(db):
    """A repeat sighting from while the row was still held never extends retention, even
    when it is written after the row expired."""
    write(db["writer"], [item(1)], retention_days=1)
    age_raw_rows(db, 30)
    assert expire(db, NOW - 20 * DAY) == 1
    # Fetched 25 days ago: after the first fetch, before the expiry, and still inside its
    # own 180-day window, so the old code would have restored it until day 155.
    seen_while_held = item(1).model_copy(update={"fetched_at": (NOW - 25 * DAY).isoformat()})
    before = states(db)
    report = write(db["writer"], [seen_while_held], retention_days=180)
    assert (report.inserted, report.already_held, report.restored, report.not_restored) == (
        0,
        0,
        0,
        1,
    )
    assert states(db) == before
    assert PostgresEvidenceReader(db["evidence_admin"], KEY).get(item(1).original_digest) is None


def test_repeat_sightings_of_held_evidence_do_not_extend_retention(db):
    write(db["writer"], [item(1)], retention_days=180)
    age_raw_rows(db, 100)
    before = states(db)
    report = write(db["writer"], [item(1)], retention_days=180)  # fetched an hour ago
    assert (report.inserted, report.already_held) == (0, 1)
    assert states(db) == before  # retain_until still 80 days away, from the first fetch
