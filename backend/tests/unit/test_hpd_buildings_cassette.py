"""End to end, offline: real resolver -> real HPD buildings retrieval, on recorded responses.

Recorded live 2026-10-22 (scripts/record_hpd_admin_fixtures.py buildings). The dataset's
rows were last updated 2026-08-12 (X-SODA2-Truth-Last-Modified: Wed, 12 Aug 2026
14:57:58 GMT); the counts match the probes in the Task 3 design.
"""

import datetime as dt
import json
from pathlib import Path

import pytest

from nyc_apartment_agent.cassette import ReplayTransport, load_cassette
from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.records.admin_models import RequestedBuildingStatus
from nyc_apartment_agent.records.hpd_buildings import retrieve_hpd_buildings
from nyc_apartment_agent.records.models import Attribution, RetrievalStatus
from nyc_apartment_agent.resolver import resolve_address
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hpd"
META = json.loads((FIXTURES / "buildings_cassette.meta.json").read_text(encoding="utf-8"))
DATA_AS_OF = "Wed, 12 Aug 2026 14:57:58 GMT"
TODAY = dt.date(2026, 10, 22)


@pytest.fixture(scope="module")
def entries():
    return load_cassette(FIXTURES / "buildings_cassette.json")


def run(key, entries):
    transport = ReplayTransport(entries)
    settings = Settings(_env_file=None)
    with geosearch_client(settings, transport) as g, socrata_client(settings, transport) as s:
        res = resolve_address(META["addresses"][key]["address"], g, s)
        result = retrieve_hpd_buildings(
            res, s, page_size=META["page_size"], digest_key=b"k", today=TODAY
        )
    assert transport.misses == []
    return res, result


def ids(result):
    return {k: v.building_ids for k, v in result.summary.by_attribution.items()}


def test_test_building_has_one_current_record(entries):
    _, result = run("west_farms_1134", entries)
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert result.provenance.data_as_of == DATA_AS_OF
    assert ids(result) == {Attribution.THIS_BUILDING: ["122027"]}
    rb = result.summary.requested_building
    assert rb.status is RequestedBuildingStatus.ONE_CURRENT_RECORD
    assert (rb.building_id, rb.legal_class_a_units, rb.legal_stories) == ("122027", 44, 6)
    assert rb.registration_id == "203625"


def test_freshness_is_stale_and_timestamps_are_kept_apart(entries):
    _, result = run("west_farms_1134", entries)
    f = result.freshness
    assert f.data_updated_at == "2026-08-12T14:57:58+00:00"
    assert f.metadata_updated_at == "2026-10-02T19:25:20+00:00"
    assert (f.stated_update_frequency, f.days_since_data_update, f.stale) == ("Monthly", 71, True)


def test_eight_building_lot_over_two_pages(entries):
    _, result = run("west_17_220", entries)
    assert [p.rows for p in result.provenance.pages] == [0, 5, 3]
    assert ids(result) == {
        Attribution.THIS_BUILDING: ["806076"],
        Attribution.OTHER_BUILDING_ON_LOT: [
            "30619",
            "805520",
            "805521",
            "805522",
            "805523",
            "806077",
            "973556",
        ],
    }
    bins = {r.building_id: r.source_bin for r in result.records}
    # The HPD building ids carried by the rows Tasks 1-2 leave unattributed:
    assert (bins["805523"], bins["30619"]) == ("1081547", "1081549")


def test_tax_lot_stays_lot_level_though_hpd_lists_a_real_bin(entries):
    res, result = run("west_farms_1217", entries)
    assert res.permitted_join_keys == ["bbl"] and res.building.bin is None
    (r,) = result.records
    assert (r.building_id, r.source_bin, r.address) == ("972462", "2128717", "1017 HOME STREET")
    assert r.attribution is Attribution.LOT_LEVEL
    rb = result.summary.requested_building
    assert rb.status is RequestedBuildingStatus.NOT_APPLICABLE_TAX_LOT


def test_non_residential_building_record(entries):
    _, result = run("fifth_ave_350", entries)
    (r,) = result.records
    assert r.attribution is Attribution.THIS_BUILDING
    assert (r.registration_id, r.registration_id_raw) == (None, "0")
    assert (r.legal_class_a_units, r.legal_class_b_units) == (0, 0)
    assert r.dob_building_class == "NOT AVAILABLE"


def test_condo_billing_lot_two_buildings(entries):
    _, result = run("acp_1845", entries)
    assert ids(result) == {
        Attribution.THIS_BUILDING: ["3376"],
        Attribution.OTHER_BUILDING_ON_LOT: ["3378"],
    }
