"""End to end, offline: real resolver -> real HPD complaints retrieval, on recorded responses.

Recorded live on 2026-10-21 (scripts/record_hpd_complaint_fixtures.py), sanitized before
saving. Counts are the live counts at that time (ygpa-z7cr version
X-SODA2-Truth-Last-Modified: Wed, 21 Oct 2026 14:10:13 GMT). They match the independent
SoQL count(*) probes recorded in docs/phase2_task2_hpd_complaints_design.md.
"""

import json
from pathlib import Path

import pytest

from nyc_apartment_agent.cassette import ReplayTransport, load_cassette
from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.records.complaint_models import ComplaintAttribution, ComplaintOutcome
from nyc_apartment_agent.records.evidence import (
    InMemoryEvidenceStore,
    verifies_original_row,
    verify,
)
from nyc_apartment_agent.records.hpd_complaints import retrieve_hpd_complaints
from nyc_apartment_agent.records.models import (
    Attribution,
    DigestBasis,
    QualityFlag,
    RetrievalStatus,
)
from nyc_apartment_agent.records.redaction import WITHHELD
from nyc_apartment_agent.resolver import resolve_address
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hpd"
META = json.loads((FIXTURES / "complaints_cassette.meta.json").read_text(encoding="utf-8"))
DATA_AS_OF = "Wed, 21 Oct 2026 14:10:13 GMT"
KEY = b"test-key"


@pytest.fixture(scope="module")
def entries():
    return load_cassette(FIXTURES / "complaints_cassette.json")


def run(key, entries, evidence=None):
    transport = ReplayTransport(entries)
    settings = Settings(_env_file=None)
    address = META["addresses"][key]["address"]
    with geosearch_client(settings, transport) as g, socrata_client(settings, transport) as s:
        resolution = resolve_address(address, g, s)
        result = retrieve_hpd_complaints(
            resolution, s, page_size=META["page_size"], evidence=evidence, digest_key=KEY
        )
    assert transport.misses == []
    return resolution, result


def problems(result):
    return {k: v.problems for k, v in result.summary.problems_by_attribution.items()}


def complaints(result):
    return {k: v.complaints for k, v in result.summary.complaints_by_attribution.items()}


def test_test_building_problems_and_complaints_counted_separately(entries):
    store = InMemoryEvidenceStore()
    res, result = run("west_farms_1134", entries, evidence=store)
    assert res.permitted_join_keys == ["bbl", "bin"]
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert result.provenance.source_count == 912 == len(result.records)
    assert len(result.complaints) == 539
    assert [p.rows for p in result.provenance.pages] == [0, 500, 412]
    assert result.provenance.data_as_of == DATA_AS_OF
    assert problems(result) == {Attribution.THIS_BUILDING: 912}
    assert complaints(result) == {ComplaintAttribution.THIS_BUILDING: 539}
    p = result.summary.problems_by_attribution[Attribution.THIS_BUILDING]
    assert (p.open, p.closed, p.duplicate_reports, p.not_flagged_duplicate) == (0, 912, 196, 716)
    c = result.summary.complaints_by_attribution[ComplaintAttribution.THIS_BUILDING]
    assert (c.open, c.closed, c.hpd_reported_violations_issued) == (0, 539, 96)
    assert result.summary.quality_flag_counts == {"date_order": 9}
    # Replay sees only sanitized fixture rows: the stored copy is intact, but it is not
    # original evidence, and the digest is not an original-row digest.
    item = store.get(result.records[0].original_digest)
    assert verify(item, KEY)
    assert not verifies_original_row(item, KEY)
    assert {r.digest_basis for r in result.records} == {DigestBasis.SANITIZED_ROW}


def test_multi_building_lot_keeps_unattributed_rows_separate(entries):
    _, result = run("west_17_220", entries)
    assert result.provenance.source_count == 105
    assert problems(result) == {
        Attribution.THIS_BUILDING: 13,
        Attribution.OTHER_BUILDING_ON_LOT: 8,
        Attribution.UNATTRIBUTED_ON_LOT: 84,
    }
    assert complaints(result) == {
        ComplaintAttribution.THIS_BUILDING: 10,
        ComplaintAttribution.OTHER_BUILDING_ON_LOT: 8,
        ComplaintAttribution.UNATTRIBUTED_ON_LOT: 61,
    }
    # The 84 rows have no bbl and no bin; they were found only by borough name+block+lot.
    unattributed = [r for r in result.records if r.attribution is Attribution.UNATTRIBUTED_ON_LOT]
    assert all(r.source_bbl is None and r.source_bin is None for r in unattributed)
    assert all(QualityFlag.BBL_MISSING in r.quality_flags for r in unattributed)


def test_tax_lot_resolution_is_lot_level_only(entries):
    res, result = run("west_farms_1217", entries)
    assert res.permitted_join_keys == ["bbl"]
    assert problems(result) == {Attribution.LOT_LEVEL: 51}
    assert complaints(result) == {ComplaintAttribution.LOT_LEVEL: 48}
    assert result.resolved_bin is None
    assert not result.summary.building_specific_claims_allowed


def test_building_with_no_violations_still_has_complaints(entries):
    _, result = run("fifth_ave_350", entries)
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert (len(result.records), len(result.complaints)) == (10, 6)
    # One row has a Manhattan BBL but borough BROOKLYN with block/lot 0: blocked.
    assert problems(result) == {Attribution.THIS_BUILDING: 9, Attribution.UNATTRIBUTED_ON_LOT: 1}
    assert result.summary.conflict_counts["lot_identifier_conflict"] == 1


def test_condo_billing_lot(entries):
    _, result = run("acp_1845", entries)
    assert [p.rows for p in result.provenance.pages] == [0, 500, 500, 500, 500, 500, 153]
    assert problems(result) == {
        Attribution.THIS_BUILDING: 1717,
        Attribution.OTHER_BUILDING_ON_LOT: 936,
    }
    assert complaints(result) == {
        ComplaintAttribution.THIS_BUILDING: 747,
        ComplaintAttribution.OTHER_BUILDING_ON_LOT: 377,
    }
    assert result.summary.conflict_counts["mixed_attribution_complaints"] == 0


@pytest.mark.parametrize("key", list(META["addresses"]))
def test_classification_coverage_is_measured_not_assumed(key, entries):
    _, result = run(key, entries)
    c = result.summary.classification
    assert c.problems_with_text == len(result.records)
    assert c.classified + c.other == c.problems_with_text
    # Rules were written from these same lots, so 100% here is in-sample (see report).
    assert c.other == 0


@pytest.mark.parametrize("key", list(META["addresses"]))
def test_no_withheld_or_unit_values_in_public_output(key, entries):
    _, result = run(key, entries)
    for r in result.records:
        assert set(r.withheld_fields) <= {"complaint_anonymous_flag", "unique_key"}
        assert r.hpd_disposition.identifies_specific_violation is False
    dumped = result.model_dump_json()
    echoed = WITHHELD in dumped  # bool first: a failing `in` on 5 MB makes pytest diff slowly
    assert not echoed  # markers are not even echoed into public output
    # Field names may appear in redaction metadata; the fields themselves never do.
    record_fields = set(type(result.records[0]).model_fields) if result.records else set()
    assert not record_fields & {"apartment", "unique_key", "complaint_anonymous_flag"}


def test_sanitized_fixture_reproduces_privacy_metrics(entries):
    """Redaction events stored with the cassette match what replay derives from markers."""
    _, result = run("west_farms_1134", entries)
    stored = [
        ev
        for url, e in entries.items()
        if "ygpa-z7cr" in url and "count" not in url
        for ev in e.get("redaction_events", [])
        if "2027540005" in url
    ]
    assert len(stored) == 912
    masked = sum(1 for ev in stored if ev["fields_masked"])
    withheld = sum(1 for ev in stored if "unique_key" in ev["withheld"])
    assert masked == result.summary.privacy.rows_with_unit_fields_masked
    assert withheld == result.summary.withheld_field_counts["unique_key"] == 752


def test_duplicate_dispositions_keep_the_initial_outcome(entries):
    _, result = run("west_farms_1134", entries)
    dups = [
        r
        for r in result.records
        if r.hpd_disposition.outcome is ComplaintOutcome.DUPLICATE_OF_BUILDING_WIDE
    ]
    assert len(dups) == 196
    assert all(r.hpd_disposition.initial_complaint_outcome is not None for r in dups)
