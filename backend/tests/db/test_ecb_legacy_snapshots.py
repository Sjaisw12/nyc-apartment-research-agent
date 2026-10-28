"""ECB snapshots saved before the 2026-10-28 adjudication revision still load, labelled.

tests/fixtures/dob311/ecb_snapshots_2026-10-23.json holds two snapshots exactly as the
code at 7445fb8 stored them (DEFAULT as upheld_by_default; CURED/IN-VIO as upheld). Each
test saves the same recorded address with the current code, then puts the old stored
JSON in place of the new (as the old code would have written it) and loads it.

Before the fix, the DEFAULT snapshot failed to load (`upheld_by_default` is not an
`Adjudication`), and the CURED/IN-VIO snapshot loaded silently with CURED counted as
upheld. Now: both load; the groups are derived again from the recorded raw hearing
status, the statement says so, and the stored classification is returned beside them.
The stored JSON and its digests are not changed by loading.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import text

from nyc_apartment_agent.records.ecb_compat import UnrecognizedAdjudicationError
from nyc_apartment_agent.records.ecb_models import Adjudication
from nyc_apartment_agent.records.ecb_violations import STATEMENT
from nyc_apartment_agent.storage.repository import load_ecb_violation_result

from . import task5_recorded as t5

pytestmark = pytest.mark.db

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "dob311" / "ecb_snapshots_2026-10-23.json"
)
LEGACY = json.loads(FIXTURE.read_text("utf-8"))
ECB = "dob_ecb_violations"
RECORD_FIELDS = ("adjudication", "adjudication_note")
SUMMARY_FIELDS = ("adjudication_notes", "statement")
BUCKET_FIELDS = ("by_adjudication", "by_correction_and_adjudication")


def _without_grouping(result_payload: dict) -> dict:
    p = copy.deepcopy(result_payload)
    for prov in p.get("bin_evidence_provenance", []):  # replay time, not data
        for page in prov.get("pages", []):
            page.pop("fetched_at")
    for f in SUMMARY_FIELDS:
        p["summary"].pop(f)
    for b in p["summary"]["by_attribution"].values():
        for f in BUCKET_FIELDS:
            b.pop(f)
    return p


def _record_data(payload: dict) -> dict:
    """A record without its grouping and replay times."""
    p = {k: copy.deepcopy(v) for k, v in payload.items() if k not in (*RECORD_FIELDS, "fetched_at")}
    for row in p.get("source_rows", []):
        row.pop("fetched_at")
    return p


def _stored(conn, fetch_id):
    f = conn.execute(
        text("select result_payload, content_sha256 from records.source_fetch where id = :f"),
        {"f": fetch_id},
    ).one()
    recs = conn.execute(
        text(
            "select ordinal, source_record_id, payload, redacted_sha256, original_digest "
            "from records.normalized_record where fetch_id = :f order by ordinal"
        ),
        {"f": fetch_id},
    ).all()
    return f, recs


def _save_as_legacy(db, key: str):
    """Save with the current code, then store the 7445fb8 JSON in its place."""
    _, saved = t5.save_recorded(db["writer"], t5.build(key))
    fetch_id = saved[ECB].fetch_id
    old = LEGACY["snapshots"][key]
    with db["owner"].begin() as conn:
        f, recs = _stored(conn, fetch_id)
        # The old snapshot is the same data: it differs only in the grouping fields.
        assert _without_grouping(f.result_payload) == _without_grouping(old["result_payload"])
        assert len(recs) == len(old["records"])
        for new, legacy in zip(recs, old["records"], strict=True):
            assert (new.ordinal, new.source_record_id) == (
                legacy["ordinal"],
                legacy["source_record_id"],
            )
            assert (new.redacted_sha256, new.original_digest) == (
                legacy["redacted_sha256"],
                legacy["original_digest"],
            )
            assert _record_data(new.payload) == _record_data(legacy["payload"])
        conn.execute(text("set local session_replication_role = replica"))
        conn.execute(
            text(
                "update records.source_fetch set result_payload = cast(:p as jsonb) where id = :f"
            ),
            {"p": json.dumps(old["result_payload"]), "f": fetch_id},
        )
        for legacy in old["records"]:
            conn.execute(
                text(
                    "update records.normalized_record set payload = cast(:p as jsonb) "
                    "where fetch_id = :f and ordinal = :o"
                ),
                {"p": json.dumps(legacy["payload"]), "f": fetch_id, "o": legacy["ordinal"]},
            )
    return fetch_id


def _assert_unchanged(db, fetch_id, key):
    old = LEGACY["snapshots"][key]
    with db["owner"].connect() as conn:
        f, recs = _stored(conn, fetch_id)
    assert f.result_payload == old["result_payload"]
    assert [r.payload for r in recs] == [r["payload"] for r in old["records"]]
    assert [r.redacted_sha256 for r in recs] == [r["redacted_sha256"] for r in old["records"]]


def test_fixture_is_the_old_grouping():
    old = LEGACY["snapshots"]
    raw = {
        (r["payload"]["hearing_status_raw"], r["payload"]["adjudication"])
        for s in old.values()
        for r in s["records"]
    }
    assert ("DEFAULT", "upheld_by_default") in raw and ("CURED/IN-VIO", "upheld") in raw
    assert LEGACY["commit"] == "7445fb8"


def test_old_default_snapshot_loads_with_the_current_reading_and_its_record(db):
    fetch_id = _save_as_legacy(db, "west_farms_1134")
    with db["app_reader"].connect() as conn:
        stored = load_ecb_violation_result(conn, fetch_id)
    reading, result = stored.adjudication_reading, stored.result
    assert (reading.stored_version, reading.current_version, reading.rederived) == (
        "2026-10-23",
        "2026-10-28",
        True,
    )
    default = next(r for r in result.records if r.hearing_status_raw == "DEFAULT")
    assert default.adjudication is Adjudication.IN_VIOLATION_BY_DEFAULT
    recorded = {r["hearing_status_raw"]: r["adjudication"] for r in reading.recorded["records"]}
    assert recorded == {
        "DEFAULT": "upheld_by_default",
        "DISMISSED": "dismissed",
        "PENDING": "pending",
    }
    bucket = result.summary.by_attribution[default.attribution]
    assert bucket.by_adjudication == {"dismissed": 1, "in_violation_by_default": 1, "pending": 1}
    old_bucket = reading.recorded["summary"]["by_attribution"][default.attribution.value]
    assert old_bucket["by_adjudication"] == {"dismissed": 1, "pending": 1, "upheld_by_default": 1}
    assert result.summary.statement.startswith(STATEMENT)
    assert "saved under the 2026-10-23 grouping" in result.summary.statement
    assert "derived again when the snapshot was loaded" in result.summary.statement
    _assert_unchanged(db, fetch_id, "west_farms_1134")


def test_old_cure_snapshot_is_not_presented_as_upheld(db):
    fetch_id = _save_as_legacy(db, "west_17_220")
    with db["app_reader"].connect() as conn:
        stored = load_ecb_violation_result(conn, fetch_id)
    result, reading = stored.result, stored.adjudication_reading
    assert reading.rederived and reading.stored_version == "2026-10-23"
    for r in result.records:  # only IN VIOLATION reads as upheld now
        assert (r.adjudication is Adjudication.UPHELD) is (r.hearing_status_raw == "IN VIOLATION")
    cured = next(r for r in result.records if r.hearing_status_raw == "CURED/IN-VIO")
    assert cured.adjudication is Adjudication.IN_VIOLATION_CURED
    old = next(r for r in reading.recorded["records"] if r["hearing_status_raw"] == "CURED/IN-VIO")
    assert old["adjudication"] == "upheld"  # what was stored, kept and labelled
    upheld_now = sum(
        b.by_adjudication.get("upheld", 0) for b in result.summary.by_attribution.values()
    )
    upheld_then = sum(
        b["by_adjudication"].get("upheld", 0)
        for b in reading.recorded["summary"]["by_attribution"].values()
    )
    assert (upheld_then, upheld_now) == (3, 2)
    assert "CURED/IN-VIO" in result.summary.adjudication_notes
    assert "upheld" not in result.summary.adjudication_notes["CURED/IN-VIO"].lower()
    _assert_unchanged(db, fetch_id, "west_17_220")


def test_new_snapshots_load_as_saved(db):
    for key in ("west_farms_1134", "fifth_ave_350", "acp_1845"):
        rec = t5.build(key)
        _, saved = t5.save_recorded(db["writer"], rec)
        with db["app_reader"].connect() as conn:
            stored = load_ecb_violation_result(conn, saved[ECB].fetch_id)
        assert stored.result == rec.ecb
        reading = stored.adjudication_reading
        assert (reading.stored_version, reading.rederived, reading.recorded) == (
            "2026-10-28",
            False,
            {},
        )
        assert "saved under the" not in stored.result.summary.statement


def test_a_snapshot_matching_no_grouping_is_refused(db):
    fetch_id = _save_as_legacy(db, "west_17_220")
    with db["owner"].begin() as conn:
        conn.execute(text("set local session_replication_role = replica"))
        conn.execute(
            text(
                "update records.normalized_record set payload = jsonb_set(payload, "
                "'{adjudication_note}', '\"edited\"') where fetch_id = :f and ordinal = 0"
            ),
            {"f": fetch_id},
        )
    with db["app_reader"].connect() as conn, pytest.raises(UnrecognizedAdjudicationError):
        load_ecb_violation_result(conn, fetch_id)


def test_a_mixed_snapshot_is_refused(db):
    """Old records under a new statement: no single grouping fits, so nothing is guessed."""
    fetch_id = _save_as_legacy(db, "west_17_220")
    with db["owner"].begin() as conn:
        conn.execute(text("set local session_replication_role = replica"))
        conn.execute(
            text(
                "update records.source_fetch set result_payload = jsonb_set(result_payload, "
                "'{summary,statement}', to_jsonb(cast(:s as text))) where id = :f"
            ),
            {"s": STATEMENT, "f": fetch_id},
        )
    with db["app_reader"].connect() as conn, pytest.raises(UnrecognizedAdjudicationError):
        load_ecb_violation_result(conn, fetch_id)
