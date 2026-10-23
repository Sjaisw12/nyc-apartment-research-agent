"""End to end, offline: real resolver -> 311 Service Requests (erm2-nwe9), on recorded data.

Replays committed recordings, unchanged on disk:
- tests/fixtures/hpd/buildings_cassette.json (2026-10-22): resolver responses;
- tests/fixtures/dob311/footprints_cassette.json and sr311_cassette.json (2026-10-23
  04:51Z, Task 5 step 1).

The committed 311 rows carry `unique_key` = [WITHHELD] and a separate `fixture_row_ref`
(FX000001..., numbered in fetch order, not derived from any key). To let the pager and
the token run, this test replaces each [WITHHELD] key IN MEMORY with a distinct
synthetic decimal built from that ref, and drops the ref. Those synthetic keys and their
tokens are test values only: they are not production identifiers and are never
compared with original evidence (every digest here has basis `sanitized_row`).

Expected (Sanyam's step-4 targets): retained rows 89 / 89 / 32 / 485 / 250. The 1134 West
Farms and 1845 ACP recordings ended with a count and data on two data versions; that
incompleteness is preserved (not forced to records_found). The HPD/DOB exclusion counts
were recorded on another version than every main fetch, so none is established.
"""

import datetime as dt
import json
from collections import Counter
from pathlib import Path
from urllib.parse import unquote_plus

import pytest

from nyc_apartment_agent.cassette import ReplayTransport, load_cassette
from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.records.evidence import InMemoryEvidenceStore, verify
from nyc_apartment_agent.records.models import (
    Attribution,
    DigestBasis,
    EvidenceKind,
    JoinScope,
    LotMembership,
    QualityFlag,
    RetrievalStatus,
)
from nyc_apartment_agent.records.redaction import K311_PATTERN
from nyc_apartment_agent.records.sr311 import retrieve_service_requests
from nyc_apartment_agent.records.sr311_models import WITHHELD_VALUE, RequestStatus
from nyc_apartment_agent.resolver import resolve_address
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HPD_META = json.loads((FIXTURES / "hpd" / "buildings_cassette.meta.json").read_text("utf-8"))
DOB_META = json.loads((FIXTURES / "dob311" / "cassettes.meta.json").read_text("utf-8"))
THU = "Thu, 22 Oct 2026 01:37:36 GMT"
FRI = "Fri, 23 Oct 2026 01:38:29 GMT"
TODAY = dt.date(2026, 10, 23)
KEY = b"cassette-test-key"
EXPECTED = {
    "west_farms_1134": 89,
    "west_17_220": 89,
    "west_farms_1217": 32,
    "fifth_ave_350": 485,
    "acp_1845": 250,
}
INCOMPLETE = {"west_farms_1134", "acp_1845"}


def _with_synthetic_keys(entries: dict) -> dict:
    """Copy of the 311 entries with distinct synthetic keys, in memory only."""
    out = {}
    for url, entry in entries.items():
        if "%24order=unique_key" not in url:
            out[url] = entry
            continue
        rows = json.loads(entry["body"])
        for r in rows:
            assert r["unique_key"] == "[WITHHELD]"
            ref = r.pop("fixture_row_ref")
            r["unique_key"] = str(900_000_000 + int(ref.removeprefix("FX")))
        out[url] = {**entry, "body": json.dumps(rows)}
    return out


@pytest.fixture(scope="module")
def results():
    hpd_entries = load_cassette(FIXTURES / "hpd" / "buildings_cassette.json")
    entries = {
        **load_cassette(FIXTURES / "dob311" / "footprints_cassette.json"),
        **_with_synthetic_keys(load_cassette(FIXTURES / "dob311" / "sr311_cassette.json")),
    }
    out = {}
    for key, address in HPD_META["addresses"].items():
        t_hpd, t_311 = ReplayTransport(hpd_entries), ReplayTransport(entries)
        settings = Settings(_env_file=None, http_max_attempts=1)
        store = InMemoryEvidenceStore()
        with (
            geosearch_client(settings, t_hpd) as g,
            socrata_client(settings, t_hpd) as s_hpd,
            socrata_client(settings, t_311) as s_311,
        ):
            res = resolve_address(address["address"], g, s_hpd)
            result = retrieve_service_requests(
                res,
                s_311,
                settings=settings,
                page_size=DOB_META["page_size"],
                digest_key=KEY,
                evidence=store,
                today=TODAY,
            )
        assert t_hpd.misses == []
        out[key] = (result, store, t_311)
    return out


def test_exact_recorded_queries_are_sent(results):
    for key, (result, _, transport) in results.items():
        assert result.provenance.where == DOB_META["addresses"][key]["sr311"]["where"], key
        assert result.provenance.order_by == "unique_key"
        # The only unrecorded request is the NULL-agency count, added after the recording.
        misses = [unquote_plus(u) for u in transport.misses]
        assert misses == [
            "https://data.cityofnewyork.us/resource/erm2-nwe9.json?$select=count(*) AS n"
            f"&$where={result.provenance.where.split(' AND agency not in')[0]}"
            " AND agency IS NULL"
        ], key
        for page in result.provenance.pages:
            url = unquote_plus(page.url)
            assert "unique_key=" not in url and ":id" not in url and "$select=*" not in url
            assert "bin" not in url.split("?", 1)[1].replace("bbl", "")


def test_retained_rows_match_the_targets(results):
    for key, (result, _, _) in results.items():
        assert len(result.records) == EXPECTED[key], key
        assert result.provenance.source_count == EXPECTED[key]
        assert result.rows_rejected == {}


def test_complete_recordings_are_records_found_on_one_version(results):
    for key in set(EXPECTED) - INCOMPLETE:
        result, _, _ = results[key]
        assert result.status is RetrievalStatus.RECORDS_FOUND, key
        assert result.accepted_data_version == FRI and result.provenance.data_as_of == FRI
        assert result.provenance.restarts == 0
        assert [(a.count_header, a.page_headers, a.accepted) for a in result.attempts] == [
            (FRI, [FRI], True)
        ]
        assert result.summary.complete


def test_two_recordings_stay_incomplete_after_two_retries(results):
    for key in INCOMPLETE:
        result, _, _ = results[key]
        assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION, key
        assert result.partial and len(result.records) == EXPECTED[key]  # rows kept
        reasons = result.provenance.incomplete_reasons
        assert reasons[0] == "dataset_changed_during_retrieval"
        assert any(r.startswith("source_snapshot_unstable") for r in reasons)
        # Replay serves the same pair each time, so all three attempts are rejected.
        assert result.provenance.restarts == 2
        assert [(a.count_header, a.page_headers, a.accepted) for a in result.attempts] == [
            (THU, [FRI], False)
        ] * 3
        assert result.data_versions_seen == [FRI, THU]
        assert result.accepted_data_version is None and result.provenance.data_as_of is None
        assert not result.summary.complete
        bucket = result.summary.by_attribution[Attribution.UNATTRIBUTED_ON_LOT]
        assert bucket.received_last_12_months is None  # windows not supportable


def test_no_exclusion_count_is_established(results):
    for key, (result, _, _) in results.items():
        ex = result.excluded_counts
        recorded = DOB_META["addresses"][key]["sr311_excluded_source_counts"]
        assert ex.by_agency.ok and ex.by_agency.truth_header == THU
        assert ex.by_agency.counts_observed == recorded["counts"]  # as returned
        assert not ex.by_agency.established and ex.by_agency.counts is None
        if key in INCOMPLETE:
            assert ex.by_agency.not_established_reason == "main fetch incomplete"
        else:
            assert ex.by_agency.not_established_reason == (
                f"data version differs: count {THU}, fetch {FRI}"
            )
        assert not ex.null_agency.established
        assert ex.null_agency.not_established_reason == "count query failed: SourceConnectionError"
        assert any(w.startswith("null_agency_coverage_unknown") for w in result.provenance.warnings)


def test_attribution_never_names_a_building(results):
    for key, (result, _, _) in results.items():
        expected = (
            Attribution.LOT_LEVEL if key == "west_farms_1217" else Attribution.UNATTRIBUTED_ON_LOT
        )
        assert {r.attribution for r in result.records} == {expected}, key
        assert result.resolved_bin is None
        assert all(r.lot_membership is LotMembership.CONFIRMED for r in result.records)
    assert results["west_farms_1217"][0].join_scope is JoinScope.TAX_LOT
    acp, _, _ = results["acp_1845"]
    assert acp.lot_family == ["1018220001", "1018227502"]
    base = Counter(r.source_bbl for r in acp.records)
    assert set(base) <= {"1018220001", "1018227502"}
    for r in acp.records:
        assert (QualityFlag.CONDO_LOT_FAMILY in r.quality_flags) is (r.source_bbl == "1018220001")


def test_tokens_evidence_and_lineage(results):
    tokens = set()
    for result, store, _ in results.values():
        for r in result.records:
            assert K311_PATTERN.match(r.request_ref) and r.source_record_id == r.request_ref
            assert r.source_row_id is None and r.collapsed_copies == 1
            assert r.evidence_kind is EvidenceKind.REPORTED_ALLEGATION
            raw = store.get(r.original_digest)
            assert raw is not None and verify(raw, KEY)
            assert raw.source_record_id == r.request_ref
            assert raw.basis is DigestBasis.SANITIZED_ROW  # fixture, not original evidence
            assert r.digest_basis is DigestBasis.SANITIZED_ROW
            assert "fixture_row_ref" not in raw.original_row
            tokens.add(r.request_ref)
    assert len(tokens) == sum(EXPECTED.values())


def test_recorded_statuses_dates_and_withheld_values(results):
    every = [r for result, _, _ in results.values() for r in result.records]
    assert Counter(r.status_raw for r in every) == {"Closed": 925, "In Progress": 19, "Pending": 1}
    assert Counter(r.request_status for r in every) == {
        RequestStatus.CLOSED: 925,
        RequestStatus.OPEN: 20,
    }
    closed_no_date = [
        r for r in every if r.request_status is RequestStatus.CLOSED and not r.closed_date.raw
    ]
    assert len(closed_no_date) == 2
    assert all(QualityFlag.STATUS_FIELDS_DISAGREE in r.quality_flags for r in closed_no_date)
    for r in every:
        for d in (r.received_date, r.closed_date, r.agency_updated_date):
            assert d.raw is None or len(d.raw) == 10
    # DCA (3 rows) and OSE (2) are not on the agency allowlist: withheld and flagged.
    assert Counter(r.agency for r in every)[WITHHELD_VALUE] == 5
    withheld = [r for r in every if r.agency == WITHHELD_VALUE]
    assert all(QualityFlag.UNCOMMON_VALUE_WITHHELD in r.quality_flags for r in withheld)


def test_no_private_field_reaches_a_public_record(results):
    for result, _, _ in results.values():
        blob = result.model_dump_json(exclude={"records": {"__all__": {"withheld_fields"}}})
        for word in ("[WITHHELD]", "incident_address", "latitude", "900000", "FX0"):
            assert word not in blob
        for r in result.records:
            assert {"unique_key", "incident_address", "location"} <= set(r.withheld_fields)


def test_withheld_values_on_the_recordings_are_counted_not_hidden(results):
    # The allowlists come from a recent window (rows since 2026-09-01) plus the dictionary,
    # so older wording is withheld (fail closed). Pinned so the effect stays visible.
    total = Counter()
    flagged = 0
    for result, _, _ in results.values():
        total.update(result.summary.withheld_value_counts)
        flagged += result.summary.quality_flag_counts.get("uncommon_value_withheld", 0)
    assert dict(total) == {
        "resolution_description": 601,
        "descriptor": 101,
        "descriptor_2": 58,
        "complaint_type": 56,
        "agency": 5,
    }
    assert flagged == 659
