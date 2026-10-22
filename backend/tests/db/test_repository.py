"""Task 4 step 3: violation results persisted as append-only snapshots, and loaded back.

Every test runs against a migrated disposable database, with real LOGIN roles.
Synthetic rows are SYNTHETIC; cassette rows are the recorded, sanitized fixtures.
"""

# ruff: noqa: S608  (table names come from the TABLES constant or the catalog; no input)

import datetime as dt
import json
import logging
import re
import urllib.parse
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from nyc_apartment_agent.cassette import ReplayTransport, load_cassette
from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.records.building_id_attribution import apply_to_violations
from nyc_apartment_agent.records.hpd_buildings import retrieve_hpd_buildings
from nyc_apartment_agent.records.hpd_violations import retrieve_hpd_violations
from nyc_apartment_agent.records.models import (
    Attribution,
    AttributionBasis,
    DigestBasis,
    RetrievalStatus,
)
from nyc_apartment_agent.resolver import resolve_address
from nyc_apartment_agent.resolver.models import Outcome, ParsedInput, ResolutionLevel
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client
from nyc_apartment_agent.storage.evidence_buffer import BufferedEvidenceStore
from nyc_apartment_agent.storage.evidence_pg import expire_due
from nyc_apartment_agent.storage.repository import (
    _INSERT_FETCH,
    _INSERT_LINK,
    _INSERT_RECORD,
    RecordWriteError,
    _fetch_params,
    _record_params,
    commit_checked,
    load_violation_result,
    save_run,
    save_violation_result,
    save_violations,
)
from tests.hpd_helpers import FakeSocrata, resolution, row

pytestmark = pytest.mark.db

KEY = b"synthetic-test-key"
TODAY = dt.date(2026, 10, 22)
SETTINGS = Settings(_env_file=None, evidence_digest_key=KEY.decode())
# Values that must never reach a readable column: unit detail and the typed address.
SENTINELS = ("SENTINEL-APT-9Z", "SENTINEL-STORY-7", "7QX", "SENTINEL-ADDR")
RAW_INPUT = "1 SENTINEL-ADDR STREET APT 7QX, BRONX"
TABLES = ("retrieval_run", "source_fetch", "fetch_page", "normalized_record", "record_source_row")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hpd"


def sentinel_rows():
    return [
        row(1),
        row(
            2,
            apartment="SENTINEL-APT-9Z",
            story="SENTINEL-STORY-7",
            novdescription="REPAIR LEAK IN APT 7QX AT 3RD STORY",
        ),
        row(3, bin=None),  # unattributed on the lot
        row(4, bin="2006186"),  # another building on the lot
        row(5, **{"class": "C"}, violationstatus="Close"),
    ]


def retrieve(rows, res=None, fake=None, page_size=10):
    buffer = BufferedEvidenceStore()
    fake = fake or FakeSocrata(rows)
    with fake.client() as c:
        result = retrieve_hpd_violations(
            res or resolution(),
            c,
            evidence=buffer,
            digest_key=KEY,
            page_size=page_size,
            today=TODAY,
        )
    return result, buffer


def save(engine, result, buffer, res=None, raw_input=RAW_INPUT):
    return save_violations(engine, res or resolution(), raw_input, result, buffer.items(), SETTINGS)


def load(engine, fetch_id):
    with engine.connect() as conn:
        return load_violation_result(conn, fetch_id)


def counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        out = {t: conn.execute(text(f"select count(*) from records.{t}")).scalar() for t in TABLES}
        out["raw_row"] = conn.execute(text("select count(*) from evidence.raw_row")).scalar()
    return out


def denied(engine, sql, **params) -> bool:
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params)
    except DBAPIError as exc:
        return "permission denied" in str(exc.orig).lower()
    return False


# ---- ownership and permissions (real logins) -------------------------------------------


def test_tables_are_owned_by_nyc_owner_and_append_only(db):
    for t in TABLES:
        owner = (
            db["owner"]
            .connect()
            .execute(
                text(
                    "select pg_get_userbyid(relowner) from pg_class "
                    "where oid = cast(:t as regclass)"
                ),
                {"t": f"records.{t}"},
            )
            .scalar()
        )
        assert owner == "nyc_owner", t
    expected = {
        "app_reader": {"SELECT"},
        "ingest_writer": {"SELECT", "INSERT"},
        "evidence_admin": set(),
        "evidence_retention": set(),
        "evidence_restorer": set(),
    }
    with db["owner"].connect() as conn:
        for role, privs in expected.items():
            for t in TABLES:
                got = {
                    p
                    for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES")
                    if conn.execute(
                        text("select has_table_privilege(:r, :t, :p)"),
                        {"r": role, "t": f"records.{t}", "p": p},
                    ).scalar()
                }
                assert got == privs, (role, t)


def test_real_logins_respect_the_grants(db):
    result, buffer = retrieve(sentinel_rows())
    run_id, saved = save(db["writer"], result, buffer)
    w, r = db["ingest_writer"], db["app_reader"]
    for t in TABLES:
        assert denied(w, f"update records.{t} set schema_version = 1") or denied(
            w, f"delete from records.{t}"
        )
        assert denied(w, f"delete from records.{t}")
        assert denied(r, f"delete from records.{t}")
        assert denied(db["evidence_admin"], f"select * from records.{t}")
        assert denied(db["evidence_retention"], f"select * from records.{t}")
    assert denied(w, "update records.source_fetch set status = 'records_found'")
    assert denied(r, "update records.normalized_record set attribution = 'this_building'")
    # app_reader can load a stored result but cannot save one.
    assert load(r, saved.fetch_id).result == result
    with pytest.raises(RecordWriteError, match="InsufficientPrivilege"):
        save(r, result, buffer)


# ---- round trip ------------------------------------------------------------------------


def test_round_trip_records_found_with_evidence_state(db):
    result, buffer = retrieve(sentinel_rows())
    assert result.status is RetrievalStatus.RECORDS_FOUND
    run_id, saved = save(db["writer"], result, buffer)
    stored = load(db["app_reader"], saved.fetch_id)
    assert stored.result == result  # every field, record order, summary and provenance
    assert stored.run_id == run_id
    assert {r.attribution for r in stored.result.records} == {
        Attribution.THIS_BUILDING,
        Attribution.UNATTRIBUTED_ON_LOT,
        Attribution.OTHER_BUILDING_ON_LOT,
    }
    assert set(stored.evidence) == {r.original_digest for r in result.records}
    assert all(s.held and s.basis is DigestBasis.ORIGINAL_ROW for s in stored.evidence.values())
    c = counts(db["owner"])
    assert c == {
        "retrieval_run": 1,
        "source_fetch": 1,
        "fetch_page": len(result.provenance.pages),
        "normalized_record": 5,
        "record_source_row": 5,
        "raw_row": 5,
    }
    with db["owner"].connect() as conn:
        f = conn.execute(text("select * from records.source_fetch")).mappings().one()
    assert (f["source_count"], f["rows_fetched"], f["record_count"], f["source_row_count"]) == (
        5,
        5,
        5,
        5,
    )


def _incomplete():
    fake = FakeSocrata([row(i) for i in range(1, 6)])
    fake.short_at = {2}  # one page comes back a row short
    return retrieve(None, fake=fake, page_size=2)


def _unavailable():
    fake = FakeSocrata([row(1)])
    fake.fail_at = {0: 503, 1: 503}
    return retrieve(None, fake=fake)


@pytest.mark.parametrize(
    ("make", "status"),
    [
        (lambda: retrieve([]), RetrievalStatus.NO_RECORDS_FOUND),
        (_incomplete, RetrievalStatus.INCOMPLETE_PAGINATION),
        (_unavailable, RetrievalStatus.SOURCE_UNAVAILABLE),
        (
            lambda: retrieve([row(1)], res=resolution(outcome=Outcome.AMBIGUOUS)),
            RetrievalStatus.UNSUPPORTED_JOIN,
        ),
        (
            lambda: retrieve(
                [row(1), row(2, bin=None)],
                res=resolution(bin_=None, level=ResolutionLevel.TAX_LOT),
            ),
            RetrievalStatus.RECORDS_FOUND,
        ),
    ],
    ids=["no_records", "incomplete", "unavailable", "unsupported_join", "tax_lot"],
)
def test_round_trip_every_status(db, make, status):
    result, buffer = make()
    assert result.status is status
    res = (
        resolution(outcome=Outcome.AMBIGUOUS)
        if status is RetrievalStatus.UNSUPPORTED_JOIN
        else (
            resolution(bin_=None, level=ResolutionLevel.TAX_LOT)
            if result.join_scope and result.join_scope.value == "tax_lot"
            else resolution()
        )
    )
    _, saved = save(db["writer"], result, buffer, res=res)
    stored = load(db["app_reader"], saved.fetch_id)
    assert stored.result == result
    assert stored.result.status is status and stored.result.partial is result.partial
    if result.join_scope and result.join_scope.value == "tax_lot":
        assert {r.attribution for r in stored.result.records} == {Attribution.LOT_LEVEL}
        assert stored.result.resolved_bin is None


# ---- recorded data, including D9 ------------------------------------------------------


@pytest.fixture(scope="module")
def cassette_entries():
    merged: dict = {}
    for n in ("violations", "buildings"):
        merged.update(load_cassette(FIXTURES / f"{n}_cassette.json"))
    return merged


def _meta(name):
    return json.loads((FIXTURES / f"{name}_cassette.meta.json").read_text("utf-8"))


def _cassette_result(key, entries):
    transport = ReplayTransport(entries)
    settings = Settings(_env_file=None)
    vmeta, bmeta = _meta("violations"), _meta("buildings")
    buffer = BufferedEvidenceStore()
    with geosearch_client(settings, transport) as g, socrata_client(settings, transport) as s:
        res = resolve_address(vmeta["addresses"][key]["address"], g, s)
        v = retrieve_hpd_violations(
            res, s, page_size=vmeta["page_size"], evidence=buffer, digest_key=KEY, today=TODAY
        )
        b = retrieve_hpd_buildings(
            res, s, page_size=bmeta["page_size"], digest_key=KEY, today=TODAY
        )
    assert transport.misses == []
    return (
        res,
        vmeta["addresses"][key]["address"],
        v,
        apply_to_violations(v, b, as_of=TODAY),
        buffer,
    )


def test_recorded_results_round_trip_before_and_after_d9(db, cassette_entries):
    keys = list(_meta("violations")["addresses"])
    assert len(keys) == 5
    upgraded_total = 0
    bases: set = set()
    for key in keys:
        res, address, before, after, buffer = _cassette_result(key, cassette_entries)
        for result in (before, after):
            _, saved = save(db["writer"], result, buffer, res=res, raw_input=address)
            stored = load(db["app_reader"], saved.fetch_id)
            assert stored.result == result, key
            # Each raw row keeps the basis its record states (fixture rows that carried
            # redaction markers are sanitized_row; untouched rows are original_row).
            for r in stored.result.records:
                assert stored.evidence[r.original_digest].basis is r.digest_basis
            bases.update(r.digest_basis for r in stored.result.records)
        d9 = load(db["app_reader"], saved.fetch_id).result
        assert d9.building_id_attribution == after.building_id_attribution
        moved = [r for r in d9.records if r.attribution_basis is AttributionBasis.HPD_BUILDING_ID]
        for r in moved:
            assert r.original_attribution is Attribution.UNATTRIBUTED_ON_LOT and r.mapped_bin
        upgraded_total += len(moved)
    assert bases == {DigestBasis.SANITIZED_ROW, DigestBasis.ORIGINAL_ROW}
    assert upgraded_total == 87  # the 220 W 17th rows (Task 3 D9 tests), stored and reloaded
    with db["owner"].connect() as conn:
        typed = conn.execute(
            text(
                "select count(*) from records.normalized_record "
                "where attribution_basis = 'hpd_building_id' "
                "and original_attribution = 'unattributed_on_lot' and mapped_bin is not null"
            )
        ).scalar()
    assert typed == 87
    # Raw rows are shared by the before and after snapshots and stored once.
    c = counts(db["owner"])
    assert c["record_source_row"] == 2 * c["raw_row"]


# ---- idempotency and append-only snapshots --------------------------------------------


def test_resave_is_idempotent_and_new_retrievals_keep_their_provenance(db):
    result, buffer = retrieve(sentinel_rows())
    with db["writer"].connect() as conn:
        run_id = save_run(conn, resolution(), RAW_INPUT, digest_key=KEY)
        first = save_violation_result(
            conn, run_id, result, buffer.items(), digest_key=KEY, retention_days=180
        )
        commit_checked(conn)
        again = save_violation_result(
            conn, run_id, result, buffer.items(), digest_key=KEY, retention_days=180
        )
        commit_checked(conn)
    assert first.created and not again.created and again.fetch_id == first.fetch_id
    one = counts(db["owner"])
    assert one["source_fetch"] == 1 and one["raw_row"] == 5
    # A genuinely new retrieval (new page fetch times) is a new snapshot in the same run.
    result2, buffer2 = retrieve(sentinel_rows())
    assert result2.provenance.pages[0].fetched_at != result.provenance.pages[0].fetched_at
    with db["writer"].connect() as conn:
        second = save_violation_result(
            conn, run_id, result2, buffer2.items(), digest_key=KEY, retention_days=180
        )
        commit_checked(conn)
    assert second.created and second.fetch_id != first.fetch_id
    two = counts(db["owner"])
    assert two["source_fetch"] == 2 and two["normalized_record"] == 10
    assert two["raw_row"] == 5  # identical source rows: same digests, stored once
    assert load(db["app_reader"], first.fetch_id).result == result
    assert load(db["app_reader"], second.fetch_id).result == result2
    # The same result under another run is that run's own snapshot.
    run2, third = save(db["writer"], result, buffer)
    assert third.created and run2 != run_id


def test_committed_snapshot_is_sealed(db):
    result, buffer = retrieve(sentinel_rows())
    _, saved = save(db["writer"], result, buffer)
    params = _record_params(saved.fetch_id, 99, result.records[0])
    with pytest.raises(DBAPIError, match="snapshot is sealed"):
        with db["ingest_writer"].begin() as conn:
            conn.execute(_INSERT_RECORD, params)
    with pytest.raises(DBAPIError, match="snapshot is sealed"):
        with db["ingest_writer"].begin() as conn:
            conn.execute(
                text(
                    "insert into records.fetch_page (fetch_id, seq, page_index, url, rows, "
                    "fetched_at, attempts) values (:f, 99, 9, "
                    "'https://data.cityofnewyork.us/resource/wvxf-dwi5.json?x=1', 0, 'x', 1)"
                ),
                {"f": saved.fetch_id},
            )


# ---- atomicity ------------------------------------------------------------------------


def test_public_constraint_violation_rolls_back_evidence_and_records(db, caplog):
    caplog.set_level(logging.DEBUG)
    result, buffer = retrieve(sentinel_rows())
    # Valid for Pydantic, invalid for SQL: the LAST record's public hash is malformed,
    # so raw rows, the run, the fetch, pages and four records are written first.
    bad_last = result.records[-1].model_copy(update={"redacted_sha256": "not-a-hash"})
    bad = result.model_copy(update={"records": [*result.records[:-1], bad_last]})
    with pytest.raises(RecordWriteError, match=r"CheckViolation \(23514\)") as info:
        save(db["writer"], bad, buffer)
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert counts(db["owner"]) == dict.fromkeys([*TABLES, "raw_row"], 0)
    for s in SENTINELS:
        assert s not in str(info.value) and s not in caplog.text


def test_commit_time_check_rolls_back_an_incomplete_snapshot(db):
    result, buffer = retrieve(sentinel_rows())
    with db["writer"].connect() as conn:
        run_id = save_run(conn, resolution(), RAW_INPUT, digest_key=KEY)
        from nyc_apartment_agent.storage.evidence_pg import write_raw_rows

        write_raw_rows(conn, buffer.items(), digest_key=KEY, retention_days=180)
        fetch_id = uuid.uuid4()
        conn.execute(_INSERT_FETCH, _fetch_params(fetch_id, run_id, "a" * 64, "b" * 64, result))
        # Records are inserted but their source-row links are not.
        conn.execute(
            _INSERT_RECORD, [_record_params(fetch_id, i, r) for i, r in enumerate(result.records)]
        )
        with pytest.raises(DBAPIError, match="stored counts do not match its rows"):
            conn.commit()
        conn.rollback()
    assert counts(db["owner"]) == dict.fromkeys([*TABLES, "raw_row"], 0)


# ---- SQL-enforced invariants, each rejected as ingest_writer ---------------------------


@pytest.fixture
def open_fetch(db):
    """A fresh writer transaction holding a valid run, raw rows and helpers."""
    result, buffer = retrieve(sentinel_rows())
    conn = db["writer"].connect()
    from nyc_apartment_agent.storage.evidence_pg import write_raw_rows

    run_id = save_run(conn, resolution(), RAW_INPUT, digest_key=KEY)
    write_raw_rows(conn, buffer.items(), digest_key=KEY, retention_days=180)
    conn.commit()
    yield conn, run_id, result
    conn.rollback()
    conn.close()


def _rejects(conn, stmt, params, pattern):
    with pytest.raises(DBAPIError, match=pattern):
        conn.execute(stmt, params)
    conn.rollback()


PAYLOAD_KEYS = {"status", "dataset_id", "partial", "join_scope", "resolved_bbl", "resolved_bin"}
PROVENANCE_KEYS = {"source_count", "rows_fetched"}


def _fetch(run_id, result, **overrides):
    """Fetch parameters with overrides applied to the typed column AND the payload, so
    each test reaches the specific constraint it names rather than payload_matches."""
    p = _fetch_params(uuid.uuid4(), run_id, uuid.uuid4().hex * 2, "c" * 64, result)
    p.update(overrides)
    payload = json.loads(json.dumps(p["result_payload"]))
    for k, v in overrides.items():
        if k in PAYLOAD_KEYS:
            payload[k] = v
        elif k in PROVENANCE_KEYS:
            payload["provenance"][k] = v
    p["result_payload"] = payload
    return p


@pytest.mark.parametrize(
    ("overrides", "pattern"),
    [
        ({"partial": True}, "records_found_is_complete"),
        ({"source_count": 0}, "records_found_is_complete"),
        ({"status": "no_records_found"}, "no_records_found_is_complete"),
        ({"status": "incomplete_pagination"}, "incomplete_is_partial"),
        ({"status": "bogus"}, "source_fetch_status_check"),
        ({"join_scope": "tax_lot"}, "bin_only_at_building_scope"),
        ({"resolved_bbl": "1000010001"}, "differ from the run"),
        ({"dataset_id": "NOT-A-DATASET"}, "source_fetch_dataset_id_check"),
        ({"record_count": 0}, "records_found_is_complete"),
        ({"source_row_count": 99}, "links_within_rows_fetched"),
    ],
)
def test_source_fetch_checks(open_fetch, overrides, pattern):
    conn, run_id, result = open_fetch
    _rejects(conn, _INSERT_FETCH, _fetch(run_id, result, **overrides), pattern)


def test_payload_must_match_typed_columns_and_hold_no_private_keys(open_fetch):
    conn, run_id, result = open_fetch
    p = _fetch(run_id, result)
    p["result_payload"] = {**p["result_payload"], "status": "no_records_found"}
    _rejects(conn, _INSERT_FETCH, p, "payload_matches")
    p = _fetch(run_id, result)
    p["result_payload"] = {**p["result_payload"], "extra": {"apartment": "x"}}
    _rejects(conn, _INSERT_FETCH, p, "payload_has_no_private_keys")
    p = _fetch(run_id, result)
    p["result_payload"] = {**p["result_payload"], "records": []}
    _rejects(conn, _INSERT_FETCH, p, "payload_matches")


def test_building_scope_fetch_needs_a_building_level_run(open_fetch):
    conn, _, result = open_fetch
    lot_run = save_run(
        conn, resolution(bin_=None, level=ResolutionLevel.TAX_LOT), RAW_INPUT, digest_key=KEY
    )
    _rejects(conn, _INSERT_FETCH, _fetch(lot_run, result), "building-scope fetch needs")


def _with_fetch(conn, run_id, result, **fetch_overrides):
    p = _fetch(run_id, result, **fetch_overrides)
    conn.execute(_INSERT_FETCH, p)
    return p["id"]


def test_records_rejected_under_a_fetch_without_records(open_fetch):
    conn, run_id, result = open_fetch
    empty, _ = retrieve([])
    p = _fetch(run_id, empty)
    conn.execute(_INSERT_FETCH, p)
    _rejects(
        conn,
        _INSERT_RECORD,
        _record_params(p["id"], 0, result.records[0]),
        "not allowed under a no_records_found fetch",
    )


def test_building_attribution_rejected_at_tax_lot_scope(open_fetch):
    conn, _, result = open_fetch
    lot_res = resolution(bin_=None, level=ResolutionLevel.TAX_LOT)
    lot_result, buffer = retrieve([row(1)], res=lot_res)
    lot_run = save_run(conn, lot_res, RAW_INPUT, digest_key=KEY)
    fid = _with_fetch(conn, lot_run, lot_result)
    rec = result.records[0]  # this_building, from a building-scope retrieval
    assert rec.attribution is Attribution.THIS_BUILDING
    _rejects(conn, _INSERT_RECORD, _record_params(fid, 0, rec), "requires a building-scope fetch")


@pytest.mark.parametrize(
    ("update", "pattern"),
    [
        ({"attribution_basis": AttributionBasis.HPD_BUILDING_ID}, "building_id_basis"),
        ({"mapped_bin": "2006185"}, "row_identifier_basis"),
        ({"original_attribution": Attribution.UNATTRIBUTED_ON_LOT}, "row_identifier_basis"),
        ({"resolved_bbl": "2027540006"}, "differs from its fetch"),
        ({"original_digest": "zz"}, "original_digest_check"),
    ],
)
def test_record_checks(open_fetch, update, pattern):
    conn, run_id, result = open_fetch
    fid = _with_fetch(conn, run_id, result)
    rec = result.records[0].model_copy(update=update)
    _rejects(conn, _INSERT_RECORD, _record_params(fid, 0, rec), pattern)


def test_record_payload_must_match_and_hold_no_private_keys(open_fetch):
    conn, run_id, result = open_fetch
    fid = _with_fetch(conn, run_id, result)
    p = _record_params(fid, 0, result.records[0])
    p["payload"] = {**p["payload"], "attribution": "lot_level"}
    _rejects(conn, _INSERT_RECORD, p, "payload_matches")
    fid = _with_fetch(conn, run_id, result)
    p = _record_params(fid, 0, result.records[0])
    p["payload"] = {**p["payload"], "dates": {"x": {"story": "3"}}}
    _rejects(conn, _INSERT_RECORD, p, "payload_has_no_private_keys")


def test_source_row_link_needs_a_stored_raw_row(open_fetch):
    conn, run_id, result = open_fetch
    fid = _with_fetch(conn, run_id, result)
    rec = _record_params(fid, 0, result.records[0])
    conn.execute(_INSERT_RECORD, rec)
    link = {
        "record_id": rec["id"],
        "fetch_id": fid,
        "source_record_id": "1",
        "original_digest": "f" * 64,
    }
    _rejects(conn, _INSERT_LINK, link, "record_source_row_original_digest_fkey")


def test_raw_row_basis_must_match_the_record_at_commit(open_fetch):
    conn, run_id, result = open_fetch
    one = result.model_copy(update={"records": result.records[:1]})
    p = _fetch(
        run_id,
        one,
        record_count=1,
        source_row_count=1,
        source_count=1,
        rows_fetched=1,
        page_count=0,
    )
    conn.execute(_INSERT_FETCH, p)
    rec = result.records[0].model_copy(update={"digest_basis": DigestBasis.SANITIZED_ROW})
    rp = _record_params(p["id"], 0, rec)
    conn.execute(_INSERT_RECORD, rp)
    conn.execute(
        _INSERT_LINK,
        {
            "record_id": rp["id"],
            "fetch_id": p["id"],
            "source_record_id": rec.violation_id,
            "original_digest": rec.original_digest,
        },
    )
    with pytest.raises(DBAPIError, match="raw row basis differs from the record"):
        conn.commit()
    conn.rollback()


def test_commit_failures_are_scrubbed(open_fetch):
    conn, run_id, result = open_fetch
    conn.execute(_INSERT_FETCH, _fetch(run_id, result))  # no pages, records or links
    with pytest.raises(RecordWriteError, match=r"CheckViolation \(23514\)") as info:
        commit_checked(conn)
    assert info.value.__cause__ is None
    conn.rollback()


def test_resolution_projection_is_whitelisted(open_fetch):
    conn, _, _ = open_fetch
    stmt = text(
        "insert into records.retrieval_run (id, input_digest, resolution_outcome, "
        "resolution_level, resolved_bbl, resolution) values "
        "(:id, :d, 'verified', 'building', '2027540005', cast(:j as jsonb))"
    )
    bad = {"outcome": "verified", "resolution_level": "building", "input": {"raw": "x"}}
    _rejects(conn, stmt, {"id": uuid.uuid4(), "d": "a" * 64, "j": json.dumps(bad)}, "projection")
    bad = {"outcome": "verified", "resolution_level": "building", "building": {"label": "x"}}
    _rejects(conn, stmt, {"id": uuid.uuid4(), "d": "a" * 64, "j": json.dumps(bad)}, "projection")


# ---- privacy -------------------------------------------------------------------------


def _privacy_resolution():
    res = resolution()
    parsed = ParsedInput(
        raw=RAW_INPUT,
        house_number="1",
        street="SENTINEL-ADDR STREET",
        street_normalized="SENTINEL-ADDR STREET",
        borough="Bronx",
        zip_code=None,
        state=None,
        notes=["apartment 7QX ignored"],
    )
    return res.model_copy(
        update={
            "input": parsed,
            "explanation": f"matched {RAW_INPUT}",
            "provenance": {"requests": [{"url": "https://geosearch/?text=SENTINEL-ADDR"}]},
        }
    )


def test_no_private_value_or_typed_address_reaches_a_readable_column(db, caplog):
    caplog.set_level(logging.DEBUG)
    res = _privacy_resolution()
    result, buffer = retrieve(sentinel_rows(), res=res)
    _, saved = save(db["writer"], result, buffer, res=res, raw_input=RAW_INPUT)
    load(db["app_reader"], saved.fetch_id)
    scanned = 0
    with db["app_reader"].connect() as conn:
        relations = conn.execute(
            text(
                "select table_schema, table_name from information_schema.tables "
                "where table_schema in ('records', 'public') order by 1, 2"
            )
        ).all()
        assert {r.table_name for r in relations} >= {*TABLES, "evidence_availability"}
        for schema, name in relations:
            for (value,) in conn.execute(text(f'select t::text from "{schema}"."{name}" t')):
                scanned += 1
                for s in SENTINELS:
                    assert s not in value, (schema, name, s)
                for key in ('"apartment":', '"story":', "complaint_anonymous_flag", "unique_key"):
                    assert key not in value, (schema, name, key)
    assert scanned >= 1 + 1 + len(result.provenance.pages) + 5 + 5 + 5
    # The private copy is where the unit detail lives, and only there.
    with db["evidence_admin"].connect() as conn:
        held = conn.execute(
            text(
                "select count(*) from evidence.raw_row "
                "where original_row->>'apartment' = 'SENTINEL-APT-9Z'"
            )
        ).scalar()
    assert held == 1
    for s in SENTINELS:
        assert s not in caplog.text


def test_stored_urls_carry_only_validated_identifiers(db):
    result, buffer = retrieve(sentinel_rows())
    _, saved = save(db["writer"], result, buffer)
    where = re.compile(r"^bbl='[1-5]\d{9}' OR \(boroid=\d AND block=\d+ AND lot=\d+\)$")
    with db["app_reader"].connect() as conn:
        urls = conn.execute(text("select url from records.fetch_page")).scalars().all()
        urls += (
            conn.execute(text("select payload->>'query_url' from records.normalized_record"))
            .scalars()
            .all()
        )
    assert urls
    for url in urls:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        assert where.match(q["$where"][0]), url
        assert set(q) <= {"$where", "$select", "$order", "$limit", "$offset"}, url


def test_input_digest_is_keyed_and_normalized(db):
    result, buffer = retrieve([])
    a, _ = save(db["writer"], result, buffer, raw_input="1  test st, Bronx")
    b, _ = save(db["writer"], result, buffer, raw_input="1 TEST ST, BRONX")
    with db["app_reader"].connect() as conn:
        digests = dict(
            conn.execute(text("select id, input_digest from records.retrieval_run")).all()
        )
    assert digests[a] == digests[b] and len(digests[a]) == 64


# ---- evidence expiry -------------------------------------------------------------------


def test_expired_evidence_still_loads_and_is_reported_as_not_held(db):
    result, buffer = retrieve(sentinel_rows())
    _, saved = save(db["writer"], result, buffer)
    later = dt.datetime.now(dt.UTC) + dt.timedelta(days=181)
    with db["evidence_retention"].begin() as conn:
        assert expire_due(conn, later) == 5
    stored = load(db["app_reader"], saved.fetch_id)
    assert stored.result == result  # lineage and public records intact
    assert all(not s.held and s.expired_at == later for s in stored.evidence.values())
    # A new retrieval of the same rows restores the originals (writer has the restorer role).
    result2, buffer2 = retrieve(sentinel_rows())
    _, saved2 = save(db["writer"], result2, buffer2)
    assert saved2.evidence.restored == 5
    assert all(s.held for s in load(db["app_reader"], saved.fetch_id).evidence.values())
