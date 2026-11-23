"""Task 5 step 5: the database refuses forged Task 5 rows, whatever Python would allow.

Each case starts from a valid synthetic result, builds the exact rows the repository
would insert, changes them (no Pydantic model is involved after that point) and inserts
them as a real ingest_writer login, the only role allowed to insert. Deferred
commit-time checks are fired with SET CONSTRAINTS ALL IMMEDIATE and every case is rolled
back, so one database serves many cases. A positive control (no change) must pass first.
Every refusal must be a check violation (SQLSTATE 23514), not some other error.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from nyc_apartment_agent.records.evidence import InMemoryEvidenceStore
from nyc_apartment_agent.records.redaction import k311_token
from nyc_apartment_agent.storage import repository as repo
from nyc_apartment_agent.storage.evidence_pg import write_raw_rows
from tests.unit import test_dob_complaints as dobt
from tests.unit import test_ecb_violations as ecbt
from tests.unit import test_sr311 as srt

pytestmark = pytest.mark.db
KEY = b"constraint-test-key"
ANOTHER_TOKEN = k311_token("60009999", KEY)


def _synthetic():
    out = {}
    store = InMemoryEvidenceStore()
    dob, _ = dobt.retrieve(
        [dobt.dob_row(1), dobt.dob_row(2, bin_=dobt.OTHER)],
        footprints=[dobt.fp_row(dobt.R), dobt.fp_row(dobt.OTHER)],
        hpd=dobt.hpd_result(bins=(dobt.R, dobt.OTHER)),
        evidence=store,
        digest_key=KEY,
    )
    out["dob"] = (dob, list(store.items.values()))
    store = InMemoryEvidenceStore()
    ecb, _ = ecbt.retrieve([ecbt.ecb_row(1), ecbt.ecb_row(2)], evidence=store, digest_key=KEY)
    out["ecb"] = (ecb, list(store.items.values()))
    store = InMemoryEvidenceStore()
    sr, _ = srt.retrieve([srt.sr_row(1), srt.sr_row(2)], evidence=store, digest_key=KEY)
    out["sr311"] = (sr, list(store.items.values()))
    return out


@pytest.fixture(scope="module")
def synthetic():
    return _synthetic()


def rows_for(result, run_id) -> dict:
    """Exactly what save_result would insert, as plain dicts."""
    kind = repo._kind_of(result)
    fetch_id = uuid.uuid4()
    content = repo.content_sha256(result)
    key = repo.idempotency_key(run_id, kind.result_type, content)
    fetch = repo._fetch_params(fetch_id, run_id, key, content, result)
    pages = [
        {
            "fetch_id": fetch_id,
            "seq": seq,
            "page_index": p.index,
            "url": p.url,
            "rows": p.rows,
            "fetched_at": p.fetched_at,
            "attempts": p.attempts,
            "truth_last_modified": p.truth_last_modified,
            "last_modified": p.last_modified,
            "request_id": p.request_id,
        }
        for seq, p in enumerate(result.provenance.pages)
    ]
    records = [
        repo._record_params(fetch_id, i, r, result.resolved_bbl)
        for i, r in enumerate(result.records)
    ]
    links = [
        link
        for rec, r in zip(records, result.records, strict=True)
        for link in repo._links(fetch_id, rec["id"], r, rec["source_record_id"])
    ]
    return {"fetch": fetch, "pages": pages, "records": records, "links": links}


_RAW = text(
    """
    INSERT INTO evidence.raw_row (original_digest, dataset_id, source_record_id, basis,
        original_row, original_sha256, redacted_sha256, query_url, fetched_at, retain_until)
    VALUES (:d, :ds, :sid, 'original_row', '{"x": "1"}', :d, :d, 'u', now(),
            now() + interval '1 day')
    """
)


def attempt(engine, result, evidence, change: Callable[[dict], None] | None = None) -> None:
    """Insert the (changed) rows as ingest_writer, fire commit checks, roll back."""
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            res = dobt.resolution()
            run_id = repo.save_run(conn, res, "SYNTHETIC", digest_key=KEY)
            write_raw_rows(conn, evidence, digest_key=KEY, retention_days=180)
            rows = copy.deepcopy(rows_for(result, run_id))
            rows["conn"] = conn
            if change is not None:
                change(rows)
            conn.execute(repo._INSERT_FETCH, rows["fetch"])
            if rows["pages"]:
                conn.execute(repo._INSERT_PAGE, rows["pages"])
            if rows["records"]:
                conn.execute(repo._INSERT_RECORD, rows["records"])
                conn.execute(repo._INSERT_LINK, rows["links"])
            conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        finally:
            tx.rollback()


def refused(engine, result, evidence, change) -> str | None:
    try:
        attempt(engine, result, evidence, change)
    except DBAPIError as exc:
        return getattr(exc.orig, "sqlstate", None)
    return None


def run_cases(db, synthetic, source: str, cases: dict) -> None:
    result, evidence = synthetic[source]
    attempt(db["ingest_writer"], result, evidence)  # positive control
    outcomes = {
        name: refused(db["ingest_writer"], result, evidence, fn) for name, fn in cases.items()
    }
    wrong = {name: state for name, state in outcomes.items() if state != "23514"}
    assert wrong == {}, wrong


def rec0(rows) -> dict:
    return rows["records"][0]


def payload(rows, i: int = 0) -> dict:
    return rows["records"][i]["payload"]


def set_id(rows, value: str) -> None:
    """Change a 311 record's id everywhere it is stored (record, payload, link)."""
    rec0(rows)["source_record_id"] = value
    payload(rows)["request_ref"] = value
    payload(rows)["source_record_id"] = value
    rows["links"][0]["source_record_id"] = value


def result_payload(rows) -> dict:
    return rows["fetch"]["result_payload"]


def excluded(rows) -> dict:
    return result_payload(rows)["excluded_counts"]


# ---- 311 -------------------------------------------------------------------------------


def _plain_key_everywhere(rows):
    set_id(rows, "60000001")


def _raw_row_under_plain_key(rows):
    rows["conn"].execute(_RAW, {"d": "a" * 64, "ds": "erm2-nwe9", "sid": "60000001"})


def _establish_after_null_rows(rows):
    ex = excluded(rows)
    ex["null_agency"].update(established=True, counts={"null_agency": 2})
    reason = "null_agency_rows_not_retrieved: 2"
    rows["fetch"].update(status="incomplete_pagination", partial=True, incomplete_reasons=[reason])
    rp = result_payload(rows)
    rp.update(status="incomplete_pagination", partial=True)
    rp["provenance"]["incomplete_reasons"] = [reason]
    rp["summary"]["complete"] = False
    # by_agency stays established: forbidden once NULL-agency rows were found


def _null_unknown_without_warning(rows):
    ex = excluded(rows)
    ex["null_agency"].update(established=False, counts=None, not_established_reason="failed")
    ex["by_agency"].update(established=False, counts=None, not_established_reason="x")


def _incomplete_but_established(rows):
    reason = "dataset_changed_during_retrieval"
    rows["fetch"].update(status="incomplete_pagination", partial=True, incomplete_reasons=[reason])
    rp = result_payload(rows)
    rp.update(status="incomplete_pagination", partial=True, accepted_data_version=None)
    rp["provenance"]["incomplete_reasons"] = [reason]
    rp["summary"]["complete"] = False


def _complete_summary_with_reasons(rows):
    _incomplete_but_established(rows)
    excluded(rows)["by_agency"].update(established=False, counts=None, not_established_reason="x")
    result_payload(rows)["summary"]["complete"] = True  # but there are reasons


SR311_CASES = {
    "plain unique_key as the id": _plain_key_everywhere,
    "token too short": lambda r: set_id(r, rec0(r)["source_record_id"][:-1]),
    "token in upper case": lambda r: set_id(r, rec0(r)["source_record_id"].upper()),
    "token with another prefix": lambda r: set_id(r, "k312_" + "0" * 32),
    "request_ref differs": lambda r: payload(r).update(request_ref=ANOTHER_TOKEN),
    "payload source_record_id differs": lambda r: payload(r).update(source_record_id=ANOTHER_TOKEN),
    "unique_key key nested": lambda r: payload(r)["redaction"].update(unique_key="60000001"),
    "incident_address key": lambda r: payload(r).update(incident_address="1 SYNTHETIC ROAD"),
    "latitude key nested": lambda r: payload(r)["received_date"].update(latitude="40.8"),
    "unlisted field": lambda r: payload(r).update(caller_note="anything"),
    "row-level source_rows": lambda r: payload(r).update(
        source_rows=[{"source_row_id": "row-x", "original_digest": rec0(r)["original_digest"]}]
    ),
    "source_row_id set": lambda r: payload(r).update(source_row_id="row-abcd.efgh"),
    "Socrata :id named in a value": lambda r: payload(r).update(descriptor="see :id row-abcd"),
    "unique_key filter in a value": lambda r: payload(r).update(
        query_url=payload(r)["query_url"] + "&unique_key=60000001"
    ),
    "bare 8-digit number": lambda r: payload(r).update(descriptor="60000001"),
    "phone number in text": lambda r: payload(r).update(resolution_description="call 212-555-0199"),
    "e-mail in text": lambda r: payload(r).update(descriptor="write to a.person@example.org"),
    "time in a public date": lambda r: payload(r)["received_date"].update(raw="2026-03-05T14:22"),
    "building attribution": lambda r: (
        rec0(r).update(attribution="this_building"),
        payload(r).update(attribution="this_building"),
    ),
    "link with a row id": lambda r: r["links"][0].update(source_row_id=rec0(r)["source_record_id"]),
    "link source id differs": lambda r: r["links"][0].update(source_record_id=ANOTHER_TOKEN),
    "raw row under the plain key": _raw_row_under_plain_key,
    "page URL pages by :id": lambda r: r["pages"][-1].update(
        url=r["pages"][-1]["url"] + "&$order=:id"
    ),
    "page URL filters by unique_key": lambda r: r["pages"][-1].update(
        url=r["pages"][-1]["url"] + "&$where=unique_key%3D60000001"
    ),
    "where clause names unique_key": lambda r: (
        r["fetch"].update(where_clause="unique_key = '60000001'"),
        result_payload(r)["provenance"].update(where="unique_key = '60000001'"),
    ),
    "BIN on the fetch": lambda r: (
        r["fetch"].update(resolved_bin="2006185"),
        result_payload(r).update(resolved_bin="2006185"),
    ),
    "building claims": lambda r: result_payload(r)["summary"].update(
        building_specific_claims_allowed=True
    ),
    "excluded counts kept after NULL-agency rows": _establish_after_null_rows,
    "unknown NULL coverage without warning": _null_unknown_without_warning,
    "established counts on an incomplete fetch": _incomplete_but_established,
    "counts without established": lambda r: excluded(r)["by_agency"].update(established=False),
    "summary says complete with reasons": _complete_summary_with_reasons,
    "a private key in the result": lambda r: result_payload(r)["provenance"].update(
        location={"latitude": "40.8"}
    ),
    "plain key in a result string": lambda r: (
        r["fetch"].update(status_detail="60000001"),
        result_payload(r).update(status_detail="60000001"),
    ),
    "copies do not add up": lambda r: payload(r).update(collapsed_copies=2),
}


def test_forged_311_rows_are_refused(db, synthetic):
    run_cases(db, synthetic, "sr311", SR311_CASES)


# ---- ECB -------------------------------------------------------------------------------


ECB_CASES = {
    "respondent_name key": lambda r: payload(r).update(respondent_name="A PERSON"),
    "respondent_street key nested": lambda r: payload(r)["infractions"][0].update(
        respondent_street="1 SYNTHETIC ROAD"
    ),
    "violation_description key": lambda r: payload(r).update(violation_description="FREE TEXT"),
    "hearing_time key": lambda r: payload(r).update(hearing_time="830"),
    "respondent key in the result": lambda r: result_payload(r).update(respondent_city="X"),
    "unlisted field": lambda r: payload(r).update(inspector_note="anything"),
    "phone number in text": lambda r: payload(r).update(adjudication_note="call 212 555 0199"),
    "reported_allegation kind": lambda r: (
        rec0(r).update(evidence_kind="reported_allegation"),
        payload(r).update(evidence_kind="reported_allegation"),
    ),
    "unknown adjudication unflagged": lambda r: payload(r).update(adjudication="unknown"),
    "law text without its code": lambda r: payload(r)["infractions"][0].update(
        code=None, law_description="SOME TEXT"
    ),
    "class severity unflagged": lambda r: payload(r).update(severity="class_2"),
    "isn differs from the record id": lambda r: payload(r).update(isn_dob_bis_extract="999"),
    "attributed by BIN mapping": lambda r: (
        rec0(r).update(attribution_basis="bin_lot_mapping"),
        payload(r).update(attribution_basis="bin_lot_mapping"),
    ),
    "source_rows dropped from payload": lambda r: payload(r).update(source_rows=[]),
    "link points at another ISN": lambda r: r["links"][0].update(
        source_record_id="999", source_row_id="999"
    ),
    "financials without claims": lambda r: (
        result_payload(r)["summary"].update(building_specific_claims_allowed=False)
        if result_payload(r)["summary"]["requested_building_financials"]
        else result_payload(r)["summary"].update(
            requested_building_financials={"summonses": 0}, building_specific_claims_allowed=False
        )
    ),
    "financials count other records": lambda r: result_payload(r)["summary"][
        "requested_building_financials"
    ].update(summonses=99),
}


def test_forged_ecb_rows_are_refused(db, synthetic):
    ecb, _ = synthetic["ecb"]
    assert ecb.summary.requested_building_financials is not None  # the control has them
    run_cases(db, synthetic, "ecb", ECB_CASES)


# ---- DOB complaints ----------------------------------------------------------------------


def _other(rows) -> int:
    return next(
        i for i, r in enumerate(rows["records"]) if r["attribution"] == "other_building_on_lot"
    )


DOB_CASES = {
    "dobrundate key": lambda r: payload(r).update(dobrundate="20261022000000"),
    "unlisted field": lambda r: payload(r).update(caller_name="A PERSON"),
    "unknown code with a description": lambda r: (
        payload(r).update(outcome="unknown_code", disposition_description="TEXT"),
        rec0(r)["quality_flags"].append("disposition_code_unknown"),
        payload(r)["quality_flags"].append("disposition_code_unknown"),
    ),
    "order said to be in effect": lambda r: payload(r).update(
        order_event={
            "kind": "vacate_full_issued",
            "action": "issued",
            "disposition_code": "Y1",
            "dob_text": "T",
            "disposition_date": None,
            "complaint_number": "1",
            "still_in_effect": "yes",
            "statement": "s",
        }
    ),
    "summary order said to be in effect": lambda r: result_payload(r)["summary"].update(
        order_events=[{"still_in_effect": "in_effect"}]
    ),
    "other building by row identifiers": lambda r: (
        r["records"][_other(r)].update(attribution_basis="row_identifiers"),
        payload(r, _other(r)).update(attribution_basis="row_identifiers"),
    ),
    "building attribution with confirmed lot": lambda r: (
        rec0(r).update(lot_membership="confirmed"),
        payload(r).update(lot_membership="confirmed"),
    ),
    "building attribution with a BIN-lot conflict": lambda r: (
        rec0(r)["quality_flags"].append("bin_lot_conflict"),
        payload(r)["quality_flags"].append("bin_lot_conflict"),
    ),
    "e-mail in the recorded address": lambda r: payload(r).update(
        recorded_address="1 a.person@example.org"
    ),
    "link under the complaint number": lambda r: r["links"][0].update(
        source_record_id=payload(r)["complaint_number"],
        source_row_id=payload(r)["complaint_number"],
    ),
    "payload source row differs from link": lambda r: payload(r)["source_rows"][0].update(
        source_row_id="row-forged"
    ),
    "record id is not its first row": lambda r: rec0(r).update(source_record_id="row-forged"),
    "311 kind under a DOB fetch": lambda r: rec0(r).update(record_kind="service_request_311"),
    "claims for a conflicting BIN": lambda r: result_payload(r).update(
        bin_lot_conflicts=[r["fetch"]["resolved_bin"]]
    ),
    "page from another dataset": lambda r: r["pages"][-1].update(
        url=r["pages"][-1]["url"].replace("eabe-havv", "5zhs-2jue")
    ),
}


def test_forged_dob_rows_are_refused(db, synthetic):
    dob, _ = synthetic["dob"]
    assert {r.attribution.value for r in dob.records} == {"this_building", "other_building_on_lot"}
    assert dob.summary.building_specific_claims_allowed
    run_cases(db, synthetic, "dob", DOB_CASES)


def test_the_original_dob_link_conflict_is_refused(db, synthetic):
    """Before step 5 the DOB adapter stored a complaint's raw row under its complaint
    number while its link carried the Socrata :id. The 0007 link-identity check refuses
    that pairing at commit; the adapter now stores each raw row under its own :id."""
    dob, evidence = synthetic["dob"]
    old_style = [
        e.model_copy(update={"source_record_id": e.original_row["complaint_number"]})
        for e in evidence
    ]
    assert refused(db["ingest_writer"], dob, old_style, None) == "23514"
    assert refused(db["ingest_writer"], dob, evidence, None) is None


def test_task5_dataset_and_type_must_agree(db, synthetic):
    sr, evidence = synthetic["sr311"]

    def as_dob(rows):
        rows["fetch"]["result_type"] = "dob_complaints"

    assert refused(db["ingest_writer"], sr, evidence, as_dob) == "23514"
