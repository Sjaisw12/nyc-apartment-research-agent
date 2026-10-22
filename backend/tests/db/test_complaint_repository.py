"""Task 4 step 4: HPD complaint problems and complaint groups as append-only snapshots.

Every test runs against a migrated disposable database with real LOGIN roles.
Synthetic rows are SYNTHETIC; cassette rows are the recorded, sanitized fixtures.
"""

# ruff: noqa: S608  (table names come from constants or the catalog; no input)

import datetime as dt
import json
import logging
import uuid
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from nyc_apartment_agent.cassette import ReplayTransport, load_cassette
from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.records.building_id_attribution import apply_to_complaints
from nyc_apartment_agent.records.complaint_models import ComplaintAttribution
from nyc_apartment_agent.records.hpd_buildings import retrieve_hpd_buildings
from nyc_apartment_agent.records.hpd_complaints import retrieve_hpd_complaints
from nyc_apartment_agent.records.models import (
    Attribution,
    AttributionBasis,
    DigestBasis,
    RetrievalStatus,
)
from nyc_apartment_agent.resolver import resolve_address
from nyc_apartment_agent.resolver.models import Outcome, ResolutionLevel
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client
from nyc_apartment_agent.storage.evidence_buffer import BufferedEvidenceStore
from nyc_apartment_agent.storage.evidence_pg import write_raw_rows
from nyc_apartment_agent.storage.repository import (
    _INSERT_FETCH,
    _INSERT_GROUP,
    _INSERT_RECORD,
    RecordWriteError,
    _fetch_params,
    _group_params,
    _record_params,
    commit_checked,
    load_complaint_result,
    save_complaints,
    save_research_snapshot,
    save_result,
    save_run,
)
from tests.hpd_helpers import FakeSocrata, complaint_row, resolution, row

pytestmark = pytest.mark.db

KEY = b"synthetic-test-key"
TODAY = dt.date(2026, 10, 22)
SETTINGS = Settings(_env_file=None, evidence_digest_key=KEY.decode())
RAW_INPUT = "1 SENTINEL-ADDR STREET APT 7QX, BRONX"
UNIQUE_KEY = "987654321012"  # a synthetic 311 request number: never public
SENTINELS = ("SENTINEL-APT-9Z", "7QX", "SENTINEL-ADDR", UNIQUE_KEY)
TABLES = (
    "retrieval_run",
    "source_fetch",
    "fetch_page",
    "complaint_group",
    "normalized_record",
    "record_source_row",
)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hpd"


def sentinel_rows():
    return [
        complaint_row(1, cid=100, apartment="SENTINEL-APT-9Z", unique_key=UNIQUE_KEY),
        complaint_row(2, cid=100, bin=None),  # complaint 100 spans two buckets: mixed
        complaint_row(3, cid=101, problem_duplicate_flag="Y", status_description=None),
        complaint_row(4, cid=102, bin="2006186", complaint_anonymous_flag="Y"),
        complaint_row(
            5,
            cid=103,
            status_description="THE TENANT IN APT 7QX REPORTED THE CONDITION WAS CORRECTED.",
        ),
    ]


def retrieve(rows, res=None, fake=None, page_size=10):
    buffer = BufferedEvidenceStore()
    fake = fake or FakeSocrata(rows)
    with fake.client() as c:
        result = retrieve_hpd_complaints(
            res or resolution(),
            c,
            evidence=buffer,
            digest_key=KEY,
            page_size=page_size,
            today=TODAY,
        )
    return result, buffer


def save(engine, result, buffer, res=None, raw_input=RAW_INPUT):
    return save_complaints(engine, res or resolution(), raw_input, result, buffer.items(), SETTINGS)


def load(engine, fetch_id):
    with engine.connect() as conn:
        return load_complaint_result(conn, fetch_id)


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


# ---- permissions -----------------------------------------------------------------------


def test_complaint_group_grants_with_real_logins(db):
    expected = {
        "app_reader": {"SELECT"},
        "ingest_writer": {"SELECT", "INSERT"},
        "evidence_admin": set(),
        "evidence_retention": set(),
        "evidence_restorer": set(),
    }
    with db["owner"].connect() as conn:
        owner = conn.execute(
            text(
                "select pg_get_userbyid(relowner) from pg_class "
                "where oid = 'records.complaint_group'::regclass"
            )
        ).scalar()
        assert owner == "nyc_owner"
        for role, privs in expected.items():
            got = {
                p
                for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
                if conn.execute(
                    text("select has_table_privilege(:r, 'records.complaint_group', :p)"),
                    {"r": role, "p": p},
                ).scalar()
            }
            assert got == privs, role
    result, buffer = retrieve(sentinel_rows())
    save(db["writer"], result, buffer)
    assert denied(db["ingest_writer"], "update records.complaint_group set attribution = 'mixed'")
    assert denied(db["ingest_writer"], "delete from records.complaint_group")
    assert denied(db["app_reader"], "delete from records.complaint_group")
    assert denied(db["evidence_admin"], "select * from records.complaint_group")


# ---- round trip ------------------------------------------------------------------------


def test_round_trip_problems_groups_and_mixed_attribution(db):
    result, buffer = retrieve(sentinel_rows())
    assert result.status is RetrievalStatus.RECORDS_FOUND
    groups = {g.complaint_id: g for g in result.complaints}
    assert groups["100"].attribution is ComplaintAttribution.MIXED
    run_id, saved = save(db["writer"], result, buffer)
    stored = load(db["app_reader"], saved.fetch_id)
    assert stored.result == result  # problems, groups, summary, provenance, in order
    assert stored.run_id == run_id
    loaded = {g.complaint_id: g for g in stored.result.complaints}
    assert loaded["100"].attribution is ComplaintAttribution.MIXED
    assert loaded["100"].problem_attributions == {"this_building": 1, "unattributed_on_lot": 1}
    assert loaded["101"].duplicate_problem_count == 1
    by_id = {r.problem_id: r for r in stored.result.records}
    assert by_id["3"].hpd_disposition.outcome.value == "no_text"
    assert by_id["3"].is_duplicate_report is True
    assert by_id["5"].hpd_disposition.text == result.records[4].hpd_disposition.text
    assert all(s.held and s.basis is DigestBasis.ORIGINAL_ROW for s in stored.evidence.values())
    assert counts(db["owner"]) == {
        "retrieval_run": 1,
        "source_fetch": 1,
        "fetch_page": len(result.provenance.pages),
        "complaint_group": 4,
        "normalized_record": 5,
        "record_source_row": 5,
        "raw_row": 5,
    }
    with db["app_reader"].connect() as conn:
        f = conn.execute(text("select * from records.source_fetch")).mappings().one()
        typed = dict(
            conn.execute(
                text("select complaint_id, attribution from records.complaint_group")
            ).all()
        )
    assert (f["source_count"], f["rows_fetched"], f["record_count"], f["group_count"]) == (
        5,
        5,
        5,
        4,
    )
    assert typed == {
        "100": "mixed",
        "101": "this_building",
        "102": "other_building_on_lot",
        "103": "this_building",
    }


def test_duplicate_and_conflicting_source_rows_keep_their_lineage(db):
    rows = sentinel_rows()
    rows += [
        dict(rows[0]),  # identical copy of problem 1: collapsed into one record
        complaint_row(6, cid=104, status_description="AN UNRECOGNISED HPD NOTE."),
        complaint_row(6, cid=104, problem_status="OPEN"),  # same problem_id, other content
    ]
    result, buffer = retrieve(rows)
    # The pager cannot prove a fetch with a repeated problem_id complete, so the result
    # is incomplete_pagination (partial), never records_found.
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION and result.partial
    assert result.provenance.incomplete_reasons == ["duplicate_problem_id_across_pages"]
    copies = [r for r in result.records if r.problem_id == "6"]
    assert len(copies) == 2
    assert all("duplicate_id_different_content" in r.quality_flags for r in copies)
    assert all(r.attribution is Attribution.UNATTRIBUTED_ON_LOT for r in copies)
    assert {r.hpd_disposition.outcome.value for r in copies} >= {"other"}
    _, saved = save(db["writer"], result, buffer)
    stored = load(db["app_reader"], saved.fetch_id)
    assert stored.result == result
    with db["app_reader"].connect() as conn:
        f = conn.execute(text("select * from records.source_fetch")).mappings().one()
        links = conn.execute(
            text(
                "select source_record_id, count(*) from records.record_source_row "
                "group by 1 order by 1"
            )
        ).all()
        group = conn.execute(
            text(
                "select attribution, problem_count from records.complaint_group "
                "where complaint_id = '104'"
            )
        ).one()
    # Source count vs normalized count: 8 rows, 7 records (one identical copy collapsed).
    assert (f["source_count"], f["rows_fetched"], f["record_count"]) == (8, 8, 7)
    assert f["source_row_count"] == 7
    assert dict(links)["6"] == 2  # both conflicting copies traced to their own raw rows
    assert tuple(group) == ("unattributed_on_lot", 2)


def _incomplete():
    fake = FakeSocrata([complaint_row(i, cid=100 + i) for i in range(1, 6)])
    fake.short_at = {2}
    return retrieve(None, fake=fake, page_size=2)


def _unavailable():
    fake = FakeSocrata([complaint_row(1)])
    fake.fail_at = {0: 503, 1: 503}
    return retrieve(None, fake=fake)


LOT = resolution(bin_=None, level=ResolutionLevel.TAX_LOT)
AMBIGUOUS = resolution(outcome=Outcome.AMBIGUOUS)


@pytest.mark.parametrize(
    ("make", "res", "status"),
    [
        (lambda: retrieve([]), None, RetrievalStatus.NO_RECORDS_FOUND),
        (_incomplete, None, RetrievalStatus.INCOMPLETE_PAGINATION),
        (_unavailable, None, RetrievalStatus.SOURCE_UNAVAILABLE),
        (
            lambda: retrieve([complaint_row(1)], res=AMBIGUOUS),
            AMBIGUOUS,
            RetrievalStatus.UNSUPPORTED_JOIN,
        ),
        (
            lambda: retrieve([complaint_row(1), complaint_row(2, bin=None)], res=LOT),
            LOT,
            RetrievalStatus.RECORDS_FOUND,
        ),
    ],
    ids=["no_records", "incomplete", "unavailable", "unsupported_join", "tax_lot"],
)
def test_round_trip_every_status(db, make, res, status):
    result, buffer = make()
    assert result.status is status
    _, saved = save(db["writer"], result, buffer, res=res)
    stored = load(db["app_reader"], saved.fetch_id)
    assert stored.result == result
    assert stored.result.partial is result.partial
    if res is LOT:
        assert {r.attribution for r in stored.result.records} == {Attribution.LOT_LEVEL}
        assert {g.attribution for g in stored.result.complaints} == {ComplaintAttribution.LOT_LEVEL}
    if status is RetrievalStatus.INCOMPLETE_PAGINATION:
        assert stored.result.records and len(stored.result.records) < 5


# ---- recorded data, including D9 ------------------------------------------------------


def _meta(name):
    return json.loads((FIXTURES / f"{name}_cassette.meta.json").read_text("utf-8"))


@pytest.fixture(scope="module")
def cassette_entries():
    merged: dict = {}
    for n in ("complaints", "buildings"):
        merged.update(load_cassette(FIXTURES / f"{n}_cassette.json"))
    return merged


def _cassette_result(key, entries):
    transport = ReplayTransport(entries)
    settings = Settings(_env_file=None)
    cmeta, bmeta = _meta("complaints"), _meta("buildings")
    address = cmeta["addresses"][key]["address"]
    buffer = BufferedEvidenceStore()
    with geosearch_client(settings, transport) as g, socrata_client(settings, transport) as s:
        res = resolve_address(address, g, s)
        c = retrieve_hpd_complaints(
            res, s, page_size=cmeta["page_size"], evidence=buffer, digest_key=KEY, today=TODAY
        )
        b = retrieve_hpd_buildings(
            res, s, page_size=bmeta["page_size"], evidence=buffer, digest_key=KEY, today=TODAY
        )
    assert transport.misses == []
    return res, address, c, apply_to_complaints(c, b, as_of=TODAY), buffer, b


def test_recorded_complaints_round_trip_before_and_after_d9(db, cassette_entries):
    keys = list(_meta("complaints")["addresses"])
    assert len(keys) == 5
    stats = {}
    for key in keys:
        res, address, before, after, buffer, buildings = _cassette_result(key, cassette_entries)
        ids = {}
        for label, result in (("before", before), ("after", after)):
            _, snap = save_research_snapshot(
                db["writer"], res, address, [buildings, result], buffer.items(), SETTINGS
            )
            saved = snap["hpd_complaints"]
            stored = load(db["app_reader"], saved.fetch_id)
            assert stored.result == result, (key, label)
            for r in stored.result.records:
                assert stored.evidence[r.original_digest].basis is r.digest_basis
            ids[label] = saved.fetch_id
        p = after.provenance
        stats[key] = {
            "status": after.status.value,
            "source_count": p.source_count,
            "problems": len(after.records),
            "complaints": len(after.complaints),
            "mixed": sum(c.attribution is ComplaintAttribution.MIXED for c in after.complaints),
            "moved": sum(
                r.attribution_basis is AttributionBasis.HPD_BUILDING_ID for r in after.records
            ),
            "fetch_after": ids["after"],
        }
    print(
        "\nrecorded complaint snapshots:",
        json.dumps(
            {k: {kk: vv for kk, vv in v.items() if kk != "fetch_after"} for k, v in stats.items()},
            indent=1,
        ),
    )
    assert sum(s["moved"] for s in stats.values()) == 84
    assert stats["west_17_220"]["moved"] == 84

    # 220 W 17th: the 84 reassigned problems point only at other buildings, keep their
    # original attribution and basis, and no group counts them for the requested building.
    fid = stats["west_17_220"]["fetch_after"]
    with db["app_reader"].connect() as conn:
        moved = conn.execute(
            text(
                "select attribution, original_attribution, mapped_bin, count(*) "
                "from records.normalized_record where fetch_id = :f "
                "and attribution_basis = 'hpd_building_id' group by 1, 2, 3 order by 3"
            ),
            {"f": fid},
        ).all()
        resolved_bin = conn.execute(
            text("select resolved_bin from records.source_fetch where id = :f"), {"f": fid}
        ).scalar()
        groups_with_moved = conn.execute(
            text(
                "select g.attribution, g.problem_attributions from records.complaint_group g "
                "where g.fetch_id = :f and exists (select 1 from records.normalized_record r "
                "where r.fetch_id = g.fetch_id and r.complaint_id = g.complaint_id "
                "and r.attribution_basis = 'hpd_building_id')"
            ),
            {"f": fid},
        ).all()
    assert sum(n for *_, n in moved) == 84
    assert {(a, o) for a, o, _, _ in moved} == {("other_building_on_lot", "unattributed_on_lot")}
    assert resolved_bin == "1081543" and resolved_bin not in {m for _, _, m, _ in moved}
    assert groups_with_moved
    for attribution, buckets in groups_with_moved:
        assert attribution in ("other_building_on_lot", "mixed")
        if attribution == "other_building_on_lot":
            assert set(buckets) == {"other_building_on_lot"}


# ---- idempotency and snapshots ---------------------------------------------------------


def test_resave_is_idempotent_and_d9_is_its_own_snapshot(db, cassette_entries):
    res, address, before, after, buffer, buildings = _cassette_result(
        "west_17_220", cassette_entries
    )
    with db["writer"].connect() as conn:
        run_id = save_run(conn, res, address, digest_key=KEY)
        kw = {"digest_key": KEY, "retention_days": 180}
        first = save_result(conn, run_id, before, buffer.items(), **kw)
        commit_checked(conn)
        again = save_result(conn, run_id, before, buffer.items(), **kw)
        b = save_result(conn, run_id, buildings, buffer.items(), **kw)
        d9 = save_result(conn, run_id, after, buffer.items(), buildings_fetch_id=b.fetch_id, **kw)
        commit_checked(conn)
    assert first.created and not again.created and again.fetch_id == first.fetch_id
    assert d9.created and d9.fetch_id != first.fetch_id
    c = counts(db["owner"])
    assert c["source_fetch"] == 3
    assert c["normalized_record"] == 2 * len(before.records) + len(buildings.records)
    assert c["complaint_group"] == len(before.complaints) + len(after.complaints)
    # Problem raw rows are shared by both snapshots and stored once.
    assert c["raw_row"] == len(before.records) + len(buildings.records)


# ---- atomicity -------------------------------------------------------------------------


def test_group_constraint_violation_rolls_back_everything(db, caplog):
    caplog.set_level(logging.DEBUG)
    result, buffer = retrieve(sentinel_rows())
    # Valid for Pydantic, invalid for SQL: more duplicate problems than problems.
    last = result.complaints[-1]
    bad_group = last.model_copy(update={"duplicate_problem_count": last.problem_count + 1})
    bad = result.model_copy(update={"complaints": [*result.complaints[:-1], bad_group]})
    with pytest.raises(RecordWriteError, match=r"CheckViolation \(23514\)") as info:
        save(db["writer"], bad, buffer)
    assert info.value.__cause__ is None
    assert counts(db["owner"]) == dict.fromkeys([*TABLES, "raw_row"], 0)
    for s in SENTINELS:
        assert s not in str(info.value) and s not in caplog.text


def test_problem_constraint_violation_after_groups_rolls_back_everything(db):
    result, buffer = retrieve(sentinel_rows())
    bad_last = result.records[-1].model_copy(update={"redacted_sha256": "not-a-hash"})
    bad = result.model_copy(update={"records": [*result.records[:-1], bad_last]})
    with pytest.raises(RecordWriteError, match="CheckViolation"):
        save(db["writer"], bad, buffer)
    assert counts(db["owner"]) == dict.fromkeys([*TABLES, "raw_row"], 0)


def test_group_claiming_an_unsupported_building_fails_at_commit(db):
    """A group may not claim this building when its problems do not support it."""
    result, buffer = retrieve(sentinel_rows())
    g = next(c for c in result.complaints if c.complaint_id == "102")  # other building
    claim = g.model_copy(
        update={
            "attribution": ComplaintAttribution.THIS_BUILDING,
            "problem_attributions": {"this_building": g.problem_count},
        }
    )
    groups = [claim if c.complaint_id == "102" else c for c in result.complaints]
    forged = result.model_copy(update={"complaints": groups})
    with db["writer"].connect() as conn:
        run_id = save_run(conn, resolution(), RAW_INPUT, digest_key=KEY)
        save_result(conn, run_id, forged, buffer.items(), digest_key=KEY, retention_days=180)
        with pytest.raises(DBAPIError, match="complaint group does not match its problem records"):
            conn.commit()
        conn.rollback()
    assert counts(db["owner"]) == dict.fromkeys([*TABLES, "raw_row"], 0)


# ---- SQL-enforced invariants, rejected as the writer -----------------------------------


@pytest.fixture
def writer_txn(db):
    result, buffer = retrieve(sentinel_rows())
    conn = db["writer"].connect()
    run_id = save_run(conn, resolution(), RAW_INPUT, digest_key=KEY)
    write_raw_rows(conn, buffer.items(), digest_key=KEY, retention_days=180)
    conn.commit()
    yield conn, run_id, result
    conn.rollback()
    conn.close()


def _fetch(run_id, result, **overrides):
    p = _fetch_params(uuid.uuid4(), run_id, uuid.uuid4().hex * 2, "c" * 64, result)
    p.update(overrides)
    return p


def _rejects(conn, stmt, params, pattern):
    with pytest.raises(DBAPIError, match=pattern):
        conn.execute(stmt, params)
    conn.rollback()


def _group(fetch_id, g, **payload_and_columns):
    p = _group_params(fetch_id, 0, g)
    for k, v in payload_and_columns.items():
        p[k] = v
        p["payload"] = {**p["payload"], k: v}
    return p


def test_mixed_flag_must_match_the_buckets(writer_txn):
    conn, run_id, result = writer_txn
    g = next(c for c in result.complaints if c.complaint_id == "100")
    f = _fetch(run_id, result)
    conn.execute(_INSERT_FETCH, f)
    _rejects(
        conn,
        _INSERT_GROUP,
        _group(f["id"], g, attribution="this_building"),
        "mixed_means_several_buckets",
    )
    f = _fetch(run_id, result)
    conn.execute(_INSERT_FETCH, f)
    single = next(c for c in result.complaints if c.complaint_id == "101")
    _rejects(
        conn,
        _INSERT_GROUP,
        _group(f["id"], single, attribution="mixed"),
        "mixed_means_several_buckets",
    )
    f = _fetch(run_id, result)
    conn.execute(_INSERT_FETCH, f)
    _rejects(
        conn,
        _INSERT_GROUP,
        _group(f["id"], single, attribution="lot_level"),
        "single_bucket_is_the_attribution",
    )


@pytest.mark.parametrize(
    ("change", "pattern"),
    [
        ({"problem_count": 7}, "bucket_counts_add_up"),
        ({"problem_attributions": {"somewhere": 1}}, "buckets_are_known"),
        ({"evidence_kind": "official_finding"}, "payload_matches"),
    ],
)
def test_group_checks(writer_txn, change, pattern):
    conn, run_id, result = writer_txn
    single = next(c for c in result.complaints if c.complaint_id == "101")
    f = _fetch(run_id, result)
    conn.execute(_INSERT_FETCH, f)
    if "evidence_kind" in change:
        p = _group_params(f["id"], 0, single)
        p["payload"] = {**p["payload"], **change}
    else:
        p = _group(f["id"], single, **change)
    _rejects(conn, _INSERT_GROUP, p, pattern)


def test_group_payload_holds_no_private_keys(writer_txn):
    conn, run_id, result = writer_txn
    f = _fetch(run_id, result)
    conn.execute(_INSERT_FETCH, f)
    p = _group_params(f["id"], 0, result.complaints[0])
    p["payload"] = {**p["payload"], "unique_key": "1"}
    _rejects(conn, _INSERT_GROUP, p, "payload_has_no_private_keys")


def test_withheld_field_counts_may_name_fields_but_only_hold_numbers(writer_txn):
    conn, run_id, result = writer_txn
    assert result.summary.withheld_field_counts  # keyed by withheld field names
    f = _fetch(run_id, result)
    f["result_payload"]["summary"]["withheld_field_counts"] = {"unique_key": UNIQUE_KEY}
    _rejects(conn, _INSERT_FETCH, f, "payload_has_no_private_keys")
    f = _fetch(run_id, result)
    f["result_payload"]["summary"]["apartment"] = "x"
    _rejects(conn, _INSERT_FETCH, f, "payload_has_no_private_keys")


def test_problem_needs_its_complaint_group(writer_txn):
    conn, run_id, result = writer_txn
    f = _fetch(run_id, result)
    conn.execute(_INSERT_FETCH, f)
    _rejects(
        conn,
        _INSERT_RECORD,
        _record_params(f["id"], 0, result.records[0]),
        "problem_has_its_complaint",
    )


def test_record_kinds_match_the_fetch(writer_txn, db):
    conn, run_id, result = writer_txn
    v_result, v_buffer = _violations()
    write_raw_rows(conn, v_buffer.items(), digest_key=KEY, retention_days=180)
    vf = _fetch(run_id, v_result)
    conn.execute(_INSERT_FETCH, vf)
    # Same lot and dataset as the fetch, so only the kind rule can reject it.
    rec = _same_dataset(_record_params(vf["id"], 0, result.records[0]), "wvxf-dwi5")
    _rejects(conn, _INSERT_RECORD, rec, "record kind does not match")
    write_raw_rows(conn, v_buffer.items(), digest_key=KEY, retention_days=180)
    cf = _fetch(run_id, result)
    conn.execute(_INSERT_FETCH, cf)
    rec = _same_dataset(_record_params(cf["id"], 0, v_result.records[0]), result.dataset_id)
    _rejects(conn, _INSERT_RECORD, rec, "record kind does not match")


def _same_dataset(params, dataset_id):
    params["dataset_id"] = dataset_id
    params["payload"] = {**params["payload"], "dataset_id": dataset_id}
    return params


def test_groups_only_under_complaint_fetches_with_records(writer_txn):
    conn, run_id, result = writer_txn
    v_result, _ = _violations()
    vf = _fetch(run_id, v_result)
    conn.execute(_INSERT_FETCH, vf)
    _rejects(
        conn,
        _INSERT_GROUP,
        _group_params(vf["id"], 0, result.complaints[0]),
        "only to a complaints fetch",
    )
    empty, _ = retrieve([])
    ef = _fetch(run_id, empty)
    conn.execute(_INSERT_FETCH, ef)
    _rejects(
        conn,
        _INSERT_GROUP,
        _group_params(ef["id"], 0, result.complaints[0]),
        "not allowed under a no_records_found fetch",
    )
    _rejects(
        conn, _INSERT_FETCH, _fetch(run_id, result, group_count=0), "groups_only_for_complaints"
    )


def test_building_group_rejected_at_tax_lot_scope(writer_txn):
    conn, _, result = writer_txn
    lot_result, _ = retrieve([complaint_row(1)], res=LOT)
    lot_run = save_run(conn, LOT, RAW_INPUT, digest_key=KEY)
    f = _fetch(lot_run, lot_result)
    conn.execute(_INSERT_FETCH, f)
    g = next(c for c in result.complaints if c.complaint_id == "101")  # this_building
    _rejects(conn, _INSERT_GROUP, _group_params(f["id"], 0, g), "requires a building-scope fetch")


def _violations():
    from nyc_apartment_agent.records.hpd_violations import retrieve_hpd_violations

    buffer = BufferedEvidenceStore()
    with FakeSocrata([row(1)]).client() as c:
        result = retrieve_hpd_violations(
            resolution(), c, evidence=buffer, digest_key=KEY, page_size=10, today=TODAY
        )
    return result, buffer


# ---- privacy -------------------------------------------------------------------------


FORBIDDEN_KEYS = {"apartment", "story", "complaint_anonymous_flag", "unique_key", "input"}


def _forbidden_keys(value, path=()):
    """Forbidden keys anywhere, except summary.withheld_field_counts (names -> counts)."""
    found = []
    if isinstance(value, dict):
        for k, v in value.items():
            here = (*path, k)
            if here[-2:] == ("summary", "withheld_field_counts"):
                assert all(isinstance(n, int) for n in v.values())
                continue
            if k in FORBIDDEN_KEYS:
                found.append(".".join(here))
            found += _forbidden_keys(v, here)
    elif isinstance(value, list):
        for v in value:
            found += _forbidden_keys(v, path)
    return found


def test_no_private_value_reaches_a_readable_column(db, caplog):
    caplog.set_level(logging.DEBUG)
    result, buffer = retrieve(sentinel_rows())
    _, saved = save(db["writer"], result, buffer)
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
            for (doc,) in conn.execute(text(f'select to_jsonb(t) from "{schema}"."{name}" t')):
                assert _forbidden_keys(doc) == [], (schema, name)
    assert scanned >= 1 + 1 + len(result.provenance.pages) + 4 + 5 + 5 + 5
    # Withheld values live only in the private raw rows.
    with db["evidence_admin"].connect() as conn:
        held = conn.execute(
            text("select count(*) from evidence.raw_row where original_row->>'unique_key' = :k"),
            {"k": UNIQUE_KEY},
        ).scalar()
    assert held == 1
    stored = load(db["app_reader"], saved.fetch_id).result
    assert sum(r.has_311_reference for r in stored.records) == 5
    assert Counter(f for r in stored.records for f in r.withheld_fields) == Counter(
        f for r in result.records for f in r.withheld_fields
    )
    for s in SENTINELS:
        assert s not in caplog.text
