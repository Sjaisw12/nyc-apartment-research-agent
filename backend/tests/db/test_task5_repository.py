"""Task 5 step 5: DOB complaints, ECB summonses and 311 requests through PostgreSQL.

Recorded data (the five step 2-4 addresses, tests/db/task5_recorded.py) and synthetic
edge cases (identical copies, NULL-agency rows), saved by the real server-side writer
login and loaded by the real app_reader login:

- round trips are exact (Pydantic equality of the whole result), counts and statuses are
  the accepted ones, 311 tokens are unchanged and no plain request key is stored in the
  records schema;
- every source-row link points at the raw row stored under the same source id;
- resaving is idempotent, separate runs stay separate, and any failure (in Python, in a
  row CHECK or at commit) rolls back the whole snapshot, evidence included;
- after the retention job expires the evidence, every snapshot still loads unchanged,
  reports its evidence as expired, keeps its digests, tokens and incompleteness, and
  re-saving or restoring follows the 0010 lifecycle.
"""

# ruff: noqa: S608  (table names come from the catalog; no input)

import datetime as dt
from collections import Counter

import pytest
from sqlalchemy import text

from nyc_apartment_agent.records.models import DigestBasis, RetrievalStatus
from nyc_apartment_agent.records.redaction import K311_PATTERN
from nyc_apartment_agent.storage import repository as repo
from nyc_apartment_agent.storage.evidence_buffer import BufferedEvidenceStore
from nyc_apartment_agent.storage.evidence_pg import PostgresEvidenceReader, write_raw_rows
from nyc_apartment_agent.storage.repository import (
    RecordWriteError,
    load_dob_complaint_result,
    load_ecb_violation_result,
    load_service_request_result,
    save_research_snapshot,
    save_result,
    save_run,
    source_row_links,
)
from nyc_apartment_agent.storage.retention import COMPLETE, run_retention
from tests.db import task5_recorded as t5
from tests.db.task5_recorded import save_recorded
from tests.db.test_evidence import age_raw_rows
from tests.db.test_snapshot_after_expiry import cited_digests, records_fingerprint

pytestmark = pytest.mark.db

TYPES = ("dob_complaints", "dob_ecb_violations", "service_requests_311")
LOADERS = {
    "dob_complaints": load_dob_complaint_result,
    "dob_ecb_violations": load_ecb_violation_result,
    "service_requests_311": load_service_request_result,
}


def load_saved(engine, saved) -> dict:
    with engine.connect() as conn:
        return {t: LOADERS[t](conn, saved[t].fetch_id) for t in TYPES}


def by_type(rec: t5.Recorded) -> dict:
    return dict(zip(TYPES, rec.results, strict=True))


def plain_311_keys(rec: t5.Recorded) -> set[str]:
    return {i.original_row["unique_key"] for i in rec.buffer.items() if i.dataset_id == "erm2-nwe9"}


def records_text(db) -> str:
    with db["owner"].connect() as conn:
        tables = list(
            conn.execute(
                text(
                    "select table_name from information_schema.tables "
                    "where table_schema = 'records'"
                )
            ).scalars()
        )
        return " ".join(
            conn.execute(
                text(f"select coalesce(string_agg(to_jsonb(x)::text, ' '), '') from records.{t} x")
            ).scalar()
            for t in tables
        )


@pytest.fixture(scope="module")
def recorded():
    return {k: t5.build(k) for k in t5.ADDRESSES}


def test_five_recorded_addresses_round_trip_exactly(db, recorded):
    keys_311 = set()
    for key, rec in recorded.items():
        run_id, saved = save_recorded(db["writer"], rec)
        loaded = load_saved(db["app_reader"], saved)
        for t, original in by_type(rec).items():
            stored = loaded[t]
            assert stored.result == original, (key, t)  # status, provenance, everything
            assert stored.run_id == run_id
            cited = {d for r in stored.result.records for d in cited_digests(r)}
            assert set(stored.evidence) == cited, (key, t)
            assert all(e.held for e in stored.evidence.values())
            for r in stored.result.records:
                assert stored.evidence[r.original_digest].basis is r.digest_basis
        # The accepted step 2-4 numbers, unchanged by storage.
        sr = loaded["service_requests_311"].result
        assert (len(sr.records), sr.status.value) == t5.SR311_EXPECTED[key]
        assert not sr.excluded_counts.by_agency.established
        assert not sr.excluded_counts.null_agency.established  # unrecorded count
        ecb = loaded["dob_ecb_violations"].result
        assert ecb.summary.total_summonses == t5.ECB_EXPECTED_TOTAL[key]
        if key in t5.DOB_EXPECTED:
            dob = loaded["dob_complaints"].result
            assert (len(dob.records), dob.status.value) == t5.DOB_EXPECTED[key]
        for r in sr.records:
            assert K311_PATTERN.match(r.request_ref) and r.source_record_id == r.request_ref
            assert r.source_row_id is None
        keys_311 |= plain_311_keys(rec)
    # No plain 311 request key anywhere in the records schema (tokens only).
    stored_text = records_text(db)
    assert len(keys_311) == sum(n for n, _ in t5.SR311_EXPECTED.values())
    assert not [k for k in keys_311 if k in stored_text]


def test_every_link_points_at_the_raw_row_with_the_same_source_id(db, recorded):
    rec = recorded["fifth_ave_350"]
    _, saved = save_recorded(db["writer"], rec)
    with db["ingest_writer"].connect() as conn:
        links = conn.execute(
            text(
                "select f.result_type, r.record_kind, r.source_record_id, l.position, "
                "l.source_record_id, l.source_row_id, e.source_record_id, e.dataset_id, "
                "r.dataset_id from records.record_source_row l "
                "join records.normalized_record r on r.id = l.record_id "
                "join records.source_fetch f on f.id = l.fetch_id "
                "join records.evidence_identity e on e.original_digest = l.original_digest"
            )
        ).all()
    results = by_type(rec)
    assert Counter(t for t, *_ in links) == {t: source_row_links(results[t]) for t in TYPES}
    for rt, kind, rec_id, pos, link_id, row_id, ev_id, ev_ds, rec_ds in links:
        assert link_id == ev_id and ev_ds == rec_ds, (rt, rec_id)
        if kind == "service_request_311":  # single-row: one plain link under the token
            assert pos == 0 and row_id is None and link_id == rec_id
            assert K311_PATTERN.match(link_id)
        else:  # row-level: each link is its own row (DOB :id, ECB ISN)
            assert row_id == link_id
            if pos == 0:
                assert link_id == rec_id
    dob_ids = {row_id for rt, *_, row_id, _, _, _ in links if rt == "dob_complaints"}
    assert all(i.startswith("row-") for i in dob_ids)  # Socrata :id, never the number


def test_resave_is_idempotent_and_separate_runs_stay_separate(db, recorded):
    rec = recorded["west_17_220"]
    with db["writer"].connect() as conn:
        run_id = save_run(conn, rec.resolution, rec.address, digest_key=t5.KEY)
        kw = {"digest_key": t5.KEY, "retention_days": 180}
        first = [save_result(conn, run_id, r, rec.buffer.items(), **kw) for r in rec.results]
        again = [save_result(conn, run_id, r, rec.buffer.items(), **kw) for r in rec.results]
        repo.commit_checked(conn)
    assert all(s.created for s in first) and not any(s.created for s in again)
    assert [s.fetch_id for s in first] == [s.fetch_id for s in again]
    _, other = save_recorded(db["writer"], rec)  # a new run: a new snapshot
    assert {s.fetch_id for s in first}.isdisjoint({s.fetch_id for s in other.values()})
    assert all(s.evidence.inserted == 0 for s in other.values())  # same raw rows, once
    with db["owner"].connect() as conn:
        assert conn.execute(text("select count(*) from records.source_fetch")).scalar() == 6
        assert conn.execute(text("select count(*) from records.retrieval_run")).scalar() == 2
        assert conn.execute(text("select count(*) from evidence.raw_row")).scalar() == len(
            rec.buffer
        )


def _row_counts(db) -> dict:
    with db["owner"].connect() as conn:
        return {
            t: conn.execute(text(f"select count(*) from {t}")).scalar()
            for t in (
                "records.retrieval_run",
                "records.source_fetch",
                "records.fetch_page",
                "records.normalized_record",
                "records.record_source_row",
                "evidence.raw_row",
            )
        }


def test_a_failure_in_python_rolls_back_the_whole_snapshot(db, recorded):
    rec = recorded["west_farms_1134"]
    sr_digests = {r.original_digest for r in rec.sr311.records}
    without_311 = [i for i in rec.buffer.items() if i.original_digest not in sr_digests]
    with pytest.raises(RecordWriteError, match="neither supplied nor stored"):
        save_research_snapshot(
            db["writer"], rec.resolution, rec.address, rec.results, without_311, t5.SETTINGS
        )
    assert set(_row_counts(db).values()) == {0}


def test_a_failure_at_commit_rolls_back_the_whole_snapshot(db, recorded, monkeypatch):
    """Failure injection: the 311 fetch claims one link too many. DOB and ECB were
    inserted first in the same transaction; the deferred check refuses at commit."""
    rec = recorded["west_17_220"]
    real = repo.source_row_links

    def off_by_one(result):
        return real(result) + (1 if result.dataset_id == "erm2-nwe9" else 0)

    monkeypatch.setattr(repo, "source_row_links", off_by_one)
    with pytest.raises(RecordWriteError, match="CheckViolation"):
        save_recorded(db["writer"], rec)
    assert set(_row_counts(db).values()) == {0}
    monkeypatch.undo()
    _, saved = save_recorded(db["writer"], rec)
    assert set(saved) == set(TYPES)


def test_a_failure_in_a_row_check_rolls_back_the_whole_snapshot(db, recorded, monkeypatch):
    """Failure injection below Pydantic: the 311 record payload gains a location key."""
    rec = recorded["west_17_220"]
    real = repo.record_payload

    def leaky(record):
        payload = real(record)
        if record.dataset_id == "erm2-nwe9":
            payload["redaction"] = {**payload["redaction"], "incident_address": "X"}
        return payload

    monkeypatch.setattr(repo, "record_payload", leaky)
    with pytest.raises(RecordWriteError, match="CheckViolation"):
        save_recorded(db["writer"], rec)
    assert set(_row_counts(db).values()) == {0}


def expire_everything(db):
    age_raw_rows(db, 181)
    report = run_retention(db["evidence_retention"])
    assert report.status == COMPLETE
    return report


def test_recorded_snapshots_survive_expiry_unchanged(db, recorded):
    saved_all = {k: save_recorded(db["writer"], rec)[1] for k, rec in recorded.items()}
    before = {k: load_saved(db["app_reader"], s) for k, s in saved_all.items()}
    fingerprint = records_fingerprint(db)
    held = sum(len(rec.buffer) for rec in recorded.values())

    report = expire_everything(db)
    assert report.expired == held

    assert records_fingerprint(db) == fingerprint  # nothing in records changed
    for key, saved in saved_all.items():
        after = load_saved(db["app_reader"], saved)
        for t in TYPES:
            stored, prior = after[t], before[key][t]
            assert stored.result == prior.result == by_type(recorded[key])[t], (key, t)
            assert set(stored.evidence) == set(prior.evidence)
            assert all(
                not e.held and e.expired_at == report.cutoff for e in stored.evidence.values()
            )
            assert {e.basis for e in stored.evidence.values()} <= {
                DigestBasis.ORIGINAL_ROW,
                DigestBasis.SANITIZED_ROW,
            }
        sr = after["service_requests_311"].result
        assert (len(sr.records), sr.status.value) == t5.SR311_EXPECTED[key]
        if key in ("west_farms_1134", "acp_1845"):  # still incomplete, never "complete"
            assert sr.status is RetrievalStatus.INCOMPLETE_PAGINATION and sr.partial
            assert sr.accepted_data_version is None and not sr.summary.complete
    reader = PostgresEvidenceReader(db["evidence_admin"], t5.KEY)
    some = recorded["acp_1845"].buffer.items()[:20]
    assert all(reader.get(i.original_digest) is None for i in some)  # tombstones only


def test_resave_and_restore_after_expiry_follow_the_lifecycle(db, recorded):
    rec = recorded["west_17_220"]
    save_recorded(db["writer"], rec)
    expire_everything(db)
    # The same recorded copies again: repeat sightings from while the rows were held.
    # The snapshot is saved against the tombstones; nothing is restored.
    _, saved = save_recorded(db["writer"], rec)
    assert all(s.evidence.restored == 0 for s in saved.values())
    assert sum(s.evidence.not_restored for s in saved.values()) == len(rec.buffer)
    loaded = load_saved(db["app_reader"], saved)
    assert all(not e.held for s in loaded.values() for e in s.evidence.values())
    # A copy fetched after the expiry restores that one row through the restore function.
    item = next(i for i in rec.buffer.items() if i.dataset_id == "erm2-nwe9")
    later = item.model_copy(update={"fetched_at": dt.datetime.now(dt.UTC).isoformat()})
    with db["writer"].begin() as conn:
        report = write_raw_rows(conn, [later], digest_key=t5.KEY, retention_days=180)
    assert report.restored == 1
    got = PostgresEvidenceReader(db["evidence_admin"], t5.KEY).get(item.original_digest)
    assert got is not None and got.source_record_id == item.source_record_id
    assert K311_PATTERN.match(got.source_record_id)


# ---- synthetic edge cases ---------------------------------------------------------------


def _save_synthetic(db, res, result, buffer):
    return save_research_snapshot(
        db["writer"], res, "SYNTHETIC", [result], buffer.items(), t5.SETTINGS
    )


def test_identical_copies_keep_their_lineage(db):
    from tests.unit import test_dob_complaints as dobt
    from tests.unit import test_ecb_violations as ecbt
    from tests.unit import test_sr311 as srt

    # DOB: two identical rows under two :ids -> one complaint, two links, two raw rows.
    buffer = BufferedEvidenceStore()
    a = dobt.dob_row(1)
    dob, _ = dobt.retrieve(
        [a, {**a, ":id": "row-synthetic-copy"}], evidence=buffer, digest_key=t5.KEY, page_size=1
    )
    (rec,) = dob.records
    assert len(rec.source_rows) == 2
    _, saved = _save_synthetic(db, dobt.resolution(), dob, buffer)
    with db["app_reader"].connect() as conn:
        assert load_dob_complaint_result(conn, saved["dob_complaints"].fetch_id).result == dob
    # ECB: the same row twice -> one summons, two links to ONE raw row (same ISN and
    # digest), each copy with its own row_index; incomplete, as its paging key repeated.
    buffer = BufferedEvidenceStore()
    row = ecbt.ecb_row(1)
    ecb, _ = ecbt.retrieve([row, dict(row)], evidence=buffer, digest_key=t5.KEY)
    assert ecb.status is RetrievalStatus.INCOMPLETE_PAGINATION
    (summons,) = ecb.records
    refs = summons.source_rows
    assert len(refs) == 2 and refs[0].original_digest == refs[1].original_digest
    assert refs[0].row_index != refs[1].row_index and len(buffer) == 1
    _, saved = _save_synthetic(db, ecbt.resolution(), ecb, buffer)
    with db["app_reader"].connect() as conn:
        assert load_ecb_violation_result(conn, saved["dob_ecb_violations"].fetch_id).result == ecb
    # 311: the same request twice -> one record with two copies, one link, one raw row;
    # incomplete, because a one-response retrieval must have distinct keys.
    buffer = BufferedEvidenceStore()
    sr, _ = srt.retrieve([srt.sr_row(1), srt.sr_row(1)], evidence=buffer, digest_key=t5.KEY)
    assert sr.status is RetrievalStatus.INCOMPLETE_PAGINATION  # a repeated key is retried
    assert [r.collapsed_copies for r in sr.records] == [2] and len(buffer) == 1
    _, saved = _save_synthetic(db, srt.resolution(), sr, buffer)
    with db["app_reader"].connect() as conn:
        stored = load_service_request_result(conn, saved["service_requests_311"].fetch_id)
    assert stored.result == sr


def test_null_agency_rows_are_stored_as_an_incomplete_result(db):
    from tests.unit import test_sr311 as srt

    buffer = BufferedEvidenceStore()
    fake = srt.Fake311([srt.sr_row(1)])
    fake.null = [{"n": "2"}]
    sr, _ = srt.retrieve(fake=fake, evidence=buffer, digest_key=t5.KEY)
    assert sr.status is RetrievalStatus.INCOMPLETE_PAGINATION
    _, saved = _save_synthetic(db, srt.resolution(), sr, buffer)
    with db["app_reader"].connect() as conn:
        stored = load_service_request_result(conn, saved["service_requests_311"].fetch_id)
    ex = stored.result.excluded_counts
    assert stored.result == sr and stored.result.partial
    assert stored.result.provenance.incomplete_reasons == ["null_agency_rows_not_retrieved: 2"]
    assert ex.null_agency.established and ex.null_agency.counts == {"null_agency": 2}
    assert not ex.by_agency.established and ex.by_agency.counts is None
    assert ex.by_agency.counts_observed == {"DOB": 1, "HPD": 3}  # observed values kept
    assert ex.by_agency.truth_header  # and their provenance


def test_unknown_null_agency_coverage_is_stored_with_its_warning(db):
    from tests.unit import test_sr311 as srt

    buffer = BufferedEvidenceStore()
    fake = srt.Fake311([srt.sr_row(1)])
    fake.fail["null"] = {0}
    sr, _ = srt.retrieve(fake=fake, evidence=buffer, digest_key=t5.KEY)
    _, saved = _save_synthetic(db, srt.resolution(), sr, buffer)
    with db["app_reader"].connect() as conn:
        stored = load_service_request_result(conn, saved["service_requests_311"].fetch_id)
    assert stored.result == sr
    assert not stored.result.excluded_counts.null_agency.established
    assert any(
        w.startswith("null_agency_coverage_unknown") for w in stored.result.provenance.warnings
    )
