"""Task 4 step 6: the raw-evidence retention job and the lifecycle rules behind it.

Every test connects as real LOGIN users. Times are injected: rows are written with the
real clock (the database refuses anything else) and then moved into the past with
`age_raw_rows`, so a cutoff can be chosen between their retention dates. All rows are
SYNTHETIC.
"""

import datetime as dt
import importlib.util
import json
import logging
import threading
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from nyc_apartment_agent.config import get_settings
from nyc_apartment_agent.storage.db import make_engine
from nyc_apartment_agent.storage.retention import (
    COMPLETE,
    FAILED,
    INCOMPLETE,
    count_due,
    run_retention,
)
from tests.db.conftest import url_for
from tests.db.test_evidence import NOW, SENTINEL, age_raw_rows, item, scalar, write
from tests.db.test_raw_row_lifecycle import states

pytestmark = pytest.mark.db

DAY = dt.timedelta(days=1)


def three_ages(db):
    """A: due 10 days ago. B: due 1 day ago. C: due in about 180 days."""
    write(db["writer"], [item(1)], retention_days=1)
    age_raw_rows(db, 11)
    write(db["writer"], [item(2)], retention_days=1)
    age_raw_rows(db, 2)
    write(db["writer"], [item(3)], retention_days=180)
    due = {
        n: scalar(
            db["owner"],
            "select retain_until from evidence.raw_row where source_record_id = :s",
            s=str(n),
        )
        for n in (1, 2, 3)
    }
    assert due[1] < NOW - 10 * DAY < due[2] < NOW < due[3]
    return due


def attempt(engine, sql, **params):
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params)
    except DBAPIError as exc:
        return str(exc.orig).splitlines()[0]
    return "committed"


# ---- the job: injected clock, idempotency, preserved metadata ---------------------------


def test_only_rows_due_at_the_cutoff_expire_and_reruns_change_nothing(db):
    due = three_ages(db)
    before = states(db)
    cutoff = NOW - 5 * DAY
    first = run_retention(db["evidence_retention"], now=cutoff)
    assert (first.status, first.expired, first.remaining_due, first.cutoff) == (
        COMPLETE,
        1,
        0,
        cutoff,
    )
    after = states(db)
    assert not after["1"]["held_content"] and after["1"]["expired_at"] == cutoff
    assert after["2"]["held_content"] and after["3"]["held_content"]
    # Digest, dataset, source id, basis, redacted hash, query URL, fetch time, retention
    # date and restore history are all unchanged; only the content went.
    assert {k: v["meta"] for k, v in after.items()} == {k: v["meta"] for k, v in before.items()}
    again = run_retention(db["evidence_retention"], now=cutoff)
    assert (again.status, again.expired) == (COMPLETE, 0)
    assert states(db) == after
    # The database clock: B is due now, C is not.
    now_run = run_retention(db["evidence_retention"])
    assert (now_run.status, now_run.expired) == (COMPLETE, 1)
    final = states(db)
    assert not final["2"]["held_content"] and final["3"]["held_content"]
    assert final["1"]["expired_at"] == cutoff  # the earlier tombstone is untouched
    assert count_due(db["evidence_retention"]) == 0
    assert final["3"]["meta"]["retain_until"] == before["3"]["meta"]["retain_until"]
    assert due[3] > NOW


def test_a_row_is_due_exactly_at_its_retention_date(db):
    due = three_ages(db)
    early = run_retention(db["evidence_retention"], now=due[1] - dt.timedelta(microseconds=1))
    assert early.expired == 0
    exact = run_retention(db["evidence_retention"], now=due[1])
    assert exact.expired == 1 and states(db)["1"]["expired_at"] == due[1]


def test_a_clock_ahead_of_the_database_is_refused_before_any_change(db):
    three_ages(db)
    before = states(db)
    report = run_retention(db["evidence_retention"], now=NOW + 400 * DAY)
    assert (report.status, report.expired, report.error) == (
        FAILED,
        0,
        "injected clock is ahead of the database",
    )
    assert states(db) == before
    with pytest.raises(ValueError, match="time zone"):
        run_retention(db["evidence_retention"], now=dt.datetime(2026, 1, 1))  # noqa: DTZ001


def test_batches_are_bounded_and_a_stopped_run_resumes(db):
    write(db["writer"], [item(n) for n in range(1, 8)], retention_days=1)
    age_raw_rows(db, 3)
    stopped = run_retention(db["evidence_retention"], batch_size=2, max_batches=2)
    assert (stopped.status, stopped.expired, stopped.batches, stopped.remaining_due) == (
        INCOMPLETE,
        4,
        2,
        3,
    )
    rest = run_retention(db["evidence_retention"], batch_size=2, max_batches=10)
    assert (rest.status, rest.expired, rest.batches, rest.remaining_due) == (COMPLETE, 3, 2, 0)
    with pytest.raises(ValueError):
        run_retention(db["evidence_retention"], batch_size=0)
    with pytest.raises(ValueError):
        run_retention(db["evidence_retention"], max_batches=10_001)


# ---- permissions ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "role",
    ["app_reader", "ingest_writer", "evidence_restorer", "writer", "evidence_admin", "owner"],
)
def test_other_logins_cannot_run_the_job(db, role):
    three_ages(db)
    before = states(db)
    report = run_retention(db[role])
    assert report.status == FAILED and report.expired == 0
    assert report.error in {
        "login lacks evidence_retention",
        "login can read raw evidence content; refused",
        "InsufficientPrivilege (42501)",
    }
    if role in ("evidence_admin", "owner"):  # could read content: refused outright
        assert report.error == "login can read raw evidence content; refused"
    assert states(db) == before


def test_a_retention_login_that_can_also_read_content_is_refused(db, login_role):
    three_ages(db)
    before = states(db)
    database = db["owner"].url.database
    user, password = login_role("evidence_retention", "evidence_admin")
    engine = make_engine(url_for(database, user, password))
    try:
        report = run_retention(engine)
    finally:
        engine.dispose()
    assert report.error == "login can read raw evidence content; refused"
    assert states(db) == before


def test_the_retention_role_still_cannot_read_content(db):
    t = db["evidence_retention"]
    write(db["writer"], [item(1)])
    for sql in (
        "select original_row from evidence.raw_row",
        "select original_sha256 from evidence.raw_row",
        "select * from evidence.raw_row",
        "select query_url from evidence.raw_row",
        "delete from evidence.raw_row",
        "insert into evidence.raw_row (original_digest) values (repeat('b', 64))",
    ):
        assert "permission denied" in attempt(t, sql).lower(), sql


# ---- failure, rollback and concurrency ----------------------------------------------------


def test_a_failure_mid_run_keeps_earlier_batches_and_rolls_back_its_own(db, caplog):
    caplog.set_level(logging.DEBUG)
    items = sorted((item(n) for n in range(1, 6)), key=lambda i: i.original_digest)
    write(db["writer"], items, retention_days=1)
    age_raw_rows(db, 3)
    third = items[2].original_digest
    # Test-only failure: the third row in expiry order raises, quoting its content.
    with db["owner"].begin() as conn:
        conn.execute(
            text(
                f"""
                create function evidence.t_fail() returns trigger language plpgsql as $f$
                begin
                    if old.original_digest = '{third}' then
                        raise exception 'injected failure %', old.original_row::text;
                    end if;
                    return new;
                end $f$;
                create trigger t_fail before update on evidence.raw_row
                for each row execute function evidence.t_fail();
                """
            )
        )
    report = run_retention(db["evidence_retention"], batch_size=2)
    assert (report.status, report.expired, report.batches, report.remaining_due) == (
        FAILED,
        2,
        1,
        None,
    )
    assert report.error == "RaiseException (P0001)"
    held = {s: v["held_content"] for s, v in states(db).items()}
    by_order = [held[i.source_record_id] for i in items]
    assert by_order == [False, False, True, True, True]  # batch 2 rolled back whole
    assert SENTINEL not in str(report) and SENTINEL not in caplog.text
    assert third not in caplog.text
    # Fix the cause and retry: the job picks up exactly where it stopped.
    with db["owner"].begin() as conn:
        conn.execute(text("drop trigger t_fail on evidence.raw_row"))
    retry = run_retention(db["evidence_retention"], batch_size=2)
    assert (retry.status, retry.expired) == (COMPLETE, 3)


def test_rows_locked_by_another_run_are_skipped_not_waited_on(db):
    items = sorted((item(n) for n in range(1, 6)), key=lambda i: i.original_digest)
    write(db["writer"], items, retention_days=1)
    age_raw_rows(db, 3)
    other = db["evidence_retention"].connect()
    tx = other.begin()
    other.execute(
        text(
            "select original_digest from evidence.raw_row where original_digest = any(:d) "
            "for update"
        ),
        {"d": [items[0].original_digest, items[1].original_digest]},
    )
    try:
        report = run_retention(db["evidence_retention"], lock_timeout_ms=1000)
        assert (report.status, report.expired, report.remaining_due) == (INCOMPLETE, 3, 2)
    finally:
        tx.rollback()
        other.close()
    after = run_retention(db["evidence_retention"])
    assert (after.status, after.expired) == (COMPLETE, 2)


def test_concurrent_runs_expire_each_row_once(db):
    write(db["writer"], [item(n) for n in range(1, 41)], retention_days=1)
    age_raw_rows(db, 3)
    reports = []

    def run():
        reports.append(run_retention(db["evidence_retention"], batch_size=3))

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r.status in (COMPLETE, INCOMPLETE) for r in reports), reports
    assert sum(r.expired for r in reports) == 40
    assert (
        scalar(db["owner"], "select count(*) from evidence.raw_row where expired_at is null") == 0
    )
    assert run_retention(db["evidence_retention"]).expired == 0


def test_the_report_and_log_carry_counts_only(db, caplog):
    caplog.set_level(logging.DEBUG)
    write(db["writer"], [item(1), item(2)], retention_days=1)
    age_raw_rows(db, 3)
    report = run_retention(db["evidence_retention"])
    assert report.status == COMPLETE
    assert set(report.as_log()) == {
        "status",
        "cutoff",
        "expired",
        "batches",
        "remaining_due",
        "error",
    }
    for i in (item(1), item(2)):
        assert i.original_digest not in caplog.text and i.original_digest not in str(report)
    assert SENTINEL not in caplog.text
    assert [r.retention for r in caplog.records if hasattr(r, "retention")] == [report.as_log()]


# ---- command-line entry point -----------------------------------------------------------


def _cli():
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_retention.py"
    spec = importlib.util.spec_from_file_location("run_retention_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_exit_codes_and_output(db, login_role, monkeypatch, capsys):
    cli = _cli()
    monkeypatch.setenv("RETENTION_DATABASE_URL", "")
    get_settings.cache_clear()
    assert cli.main([]) == 2
    write(db["writer"], [item(1), item(2)], retention_days=1)
    age_raw_rows(db, 3)
    user, password = login_role("evidence_retention")
    monkeypatch.setenv("RETENTION_DATABASE_URL", url_for(db["owner"].url.database, user, password))
    get_settings.cache_clear()
    try:
        capsys.readouterr()
        assert cli.main(["--dry-run"]) == 0
        assert json.loads(capsys.readouterr().out) == {"status": "dry_run", "due": 2}
        assert cli.main(["--batch-size", "1", "--max-batches", "1"]) == 3  # incomplete
        out = json.loads(capsys.readouterr().out)
        assert (out["status"], out["expired"], out["remaining_due"]) == (INCOMPLETE, 1, 1)
        assert cli.main([]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == COMPLETE
        assert password not in capsys.readouterr().out
        # A login without the role: exit 1, sanitized.
        monkeypatch.setenv(
            "RETENTION_DATABASE_URL",
            url_for(db["owner"].url.database, *login_role("app_reader")),
        )
        get_settings.cache_clear()
        assert cli.main([]) == 1
        assert json.loads(capsys.readouterr().out)["status"] == FAILED
    finally:
        monkeypatch.delenv("RETENTION_DATABASE_URL")
        get_settings.cache_clear()
