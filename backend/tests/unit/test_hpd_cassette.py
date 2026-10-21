"""End to end, offline: real resolver -> real HPD retrieval, on recorded live responses.

The cassette was recorded from the live APIs on 2026-10-21 (scripts/record_hpd_fixtures.py)
with apartment/floor detail redacted. Counts below are the live counts at that time
(dataset version X-SODA2-Truth-Last-Modified: Wed, 21 Oct 2026 14:51:46 GMT).
"""

import json
from pathlib import Path

import pytest

from nyc_apartment_agent.cassette import ReplayTransport, load_cassette
from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.records.evidence import InMemoryEvidenceStore, verify
from nyc_apartment_agent.records.hpd_violations import retrieve_hpd_violations
from nyc_apartment_agent.records.models import Attribution, QualityFlag, RetrievalStatus
from nyc_apartment_agent.resolver import resolve_address
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hpd"
META = json.loads((FIXTURES / "violations_cassette.meta.json").read_text(encoding="utf-8"))
DATA_AS_OF = "Wed, 21 Oct 2026 14:51:46 GMT"


@pytest.fixture(scope="module")
def entries():
    return load_cassette(FIXTURES / "violations_cassette.json")


def run(key, entries, evidence=None):
    transport = ReplayTransport(entries)
    settings = Settings(_env_file=None)
    address = META["addresses"][key]["address"]
    with geosearch_client(settings, transport) as g, socrata_client(settings, transport) as s:
        resolution = resolve_address(address, g, s)
        result = retrieve_hpd_violations(
            resolution, s, page_size=META["page_size"], evidence=evidence
        )
    assert transport.misses == []  # nothing outside the recording was requested
    return resolution, result


def buckets(result):
    return {k: v.records for k, v in result.summary.by_attribution.items()}


def test_test_building_complete_over_three_pages(entries):
    store = InMemoryEvidenceStore()
    res, result = run("west_farms_1134", entries, evidence=store)
    assert res.permitted_join_keys == ["bbl", "bin"]
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert result.provenance.source_count == 666 == len(result.records)
    assert [p.rows for p in result.provenance.pages] == [0, 250, 250, 166]
    assert result.provenance.data_as_of == DATA_AS_OF
    assert buckets(result) == {Attribution.THIS_BUILDING: 666}
    b = result.summary.by_attribution[Attribution.THIS_BUILDING]
    assert (b.open, b.closed) == (112, 554)
    assert b.hazard_class_counts == {"A": 131, "B": 307, "C": 227}
    assert b.open_hazard_class_counts == {"A": 9, "B": 62, "C": 40}
    assert b.information_order_counts == {"alternative_enforcement_program": 1}
    assert [o["violation_id"] for o in b.consequential_orders] == ["18617994"]
    assert result.summary.conflict_counts == {
        "duplicate_id_different_content": 0,
        "lot_identifier_conflict": 0,
        "status_fields_disagree": 0,
    }
    # Every public record traces to stored raw evidence.
    for r in result.records:
        item = store.get(r.original_sha256)
        assert item is not None and verify(item)
        assert item.redacted_sha256 == r.redacted_sha256
        assert item.source_record_id == r.violation_id
        assert r.query_url.startswith("https://data.cityofnewyork.us/resource/wvxf-dwi5.json")


def test_multi_building_lot_partitions_and_keeps_missing_bbl_rows(entries):
    res, result = run("west_17_220", entries)
    assert (res.building.bbl, res.building.bin) == ("1007660055", "1081543")
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert result.provenance.source_count == 174 == len(result.records)
    assert buckets(result) == {
        Attribution.THIS_BUILDING: 24,
        Attribution.OTHER_BUILDING_ON_LOT: 63,
        Attribution.UNATTRIBUTED_ON_LOT: 87,
    }
    no_bbl = [r for r in result.records if QualityFlag.BBL_MISSING in r.quality_flags]
    assert len(no_bbl) == 87  # retrieved only because the query includes boro-block-lot
    assert all(r.attribution is Attribution.UNATTRIBUTED_ON_LOT for r in no_bbl)
    other_bins = {
        r.source_bin for r in result.records if r.attribution.value == "other_building_on_lot"
    }
    assert "1081543" not in other_bins and len(other_bins) == 5


def test_tax_lot_resolution_only_gives_lot_level_records(entries):
    res, result = run("west_farms_1217", entries)
    assert res.permitted_join_keys == ["bbl"]
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert buckets(result) == {Attribution.LOT_LEVEL: 12}
    assert result.resolved_bin is None
    assert not result.summary.building_specific_claims_allowed


def test_building_with_no_violations(entries):
    _, result = run("fifth_ave_350", entries)
    assert result.status is RetrievalStatus.NO_RECORDS_FOUND
    assert result.provenance.source_count == 0 and result.records == []


def test_condo_billing_lot(entries):
    res, result = run("acp_1845", entries)
    assert res.building.bbl == "1018227502"  # billing lot, as PAD returns it
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert result.provenance.source_count == 1160
    assert buckets(result) == {
        Attribution.THIS_BUILDING: 891,
        Attribution.OTHER_BUILDING_ON_LOT: 269,
    }


def test_cassette_without_hpd_entries_gives_source_unavailable(entries):
    trimmed = {k: v for k, v in entries.items() if "wvxf-dwi5" not in k}
    transport = ReplayTransport(trimmed)
    settings = Settings(_env_file=None, http_max_attempts=1)
    with geosearch_client(settings, transport) as g, socrata_client(settings, transport) as s:
        resolution = resolve_address(META["addresses"]["west_farms_1134"]["address"], g, s)
        result = retrieve_hpd_violations(resolution, s, page_size=META["page_size"])
    assert result.status is RetrievalStatus.SOURCE_UNAVAILABLE
