"""End to end, offline: resolver -> HPD buildings -> HPD registrations, on recorded data.

Recorded live 2026-10-22 (scripts/record_hpd_admin_fixtures.py registrations). The
registrations dataset's rows were last updated 2026-08-12; every registration on these
lots ends 2026-09-01, so on 2026-10-22 the snapshot cannot show whether they renewed.
"""

import datetime as dt
import json
from pathlib import Path

import pytest

from nyc_apartment_agent.cassette import ReplayTransport, load_cassette
from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.records.admin_models import RegistrationStatus
from nyc_apartment_agent.records.hpd_buildings import retrieve_hpd_buildings
from nyc_apartment_agent.records.hpd_registrations import retrieve_hpd_registrations
from nyc_apartment_agent.records.models import Attribution, RetrievalStatus
from nyc_apartment_agent.resolver import resolve_address
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hpd"
META = json.loads((FIXTURES / "registrations_cassette.meta.json").read_text(encoding="utf-8"))
TODAY = dt.date(2026, 10, 22)
STALE = RegistrationStatus.ON_FILE_END_DATE_PASSED_AFTER_DATA_UPDATE


@pytest.fixture(scope="module")
def entries():
    return load_cassette(FIXTURES / "registrations_cassette.json")


def run(key, entries):
    transport = ReplayTransport(entries)
    settings = Settings(_env_file=None)
    with geosearch_client(settings, transport) as g, socrata_client(settings, transport) as s:
        res = resolve_address(META["addresses"][key]["address"], g, s)
        kw = {"page_size": META["page_size"], "digest_key": b"k", "today": TODAY}
        b = retrieve_hpd_buildings(res, s, **kw)
        result = retrieve_hpd_registrations(res, s, buildings=b, **kw)
    assert transport.misses == []
    return res, result


def test_test_building_registration_status_reflects_stale_snapshot(entries):
    _, result = run("west_farms_1134", entries)
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert result.provenance.data_as_of == "Wed, 12 Aug 2026 15:00:38 GMT"
    rb = result.summary.requested_building
    assert (rb.building_id, rb.latest_registration_id, rb.status) == ("122027", "203625", STALE)
    assert rb.latest_end_date.value == dt.date(2026, 9, 1)
    assert rb.latest_last_registration_date.value == dt.date(2025, 10, 8)
    assert rb.status_basis["data_updated_on"] == "2026-08-12"
    assert result.freshness.metadata_updated_at.startswith("2026-10-01")


def test_eight_registrations_joined_by_building_id(entries):
    _, result = run("west_17_220", entries)
    assert [p.rows for p in result.provenance.pages] == [0, 5, 3]
    by_building = {
        b.building_id: (b.attribution, b.registration_ids) for b in result.summary.buildings
    }
    assert by_building["806076"] == (Attribution.THIS_BUILDING, ["123116"])
    assert by_building["805523"] == (Attribution.OTHER_BUILDING_ON_LOT, ["121980"])
    assert by_building["30619"] == (Attribution.OTHER_BUILDING_ON_LOT, ["121982"])
    assert len(by_building) == 8 and result.summary.quality_flag_counts == {}
    assert {b.status for b in result.summary.buildings} == {STALE}


def test_tax_lot_registration_is_lot_level(entries):
    _, result = run("west_farms_1217", entries)
    assert [b.attribution for b in result.summary.buildings] == [Attribution.LOT_LEVEL]
    assert result.summary.requested_building is None


def test_no_registration_on_file_is_only_that(entries):
    _, result = run("fifth_ave_350", entries)
    assert result.status is RetrievalStatus.NO_RECORDS_FOUND
    rb = result.summary.requested_building
    assert (rb.status, rb.building_id) == (RegistrationStatus.NONE_ON_FILE, "2780")


def test_condo_lot_two_registrations(entries):
    _, result = run("acp_1845", entries)
    by_building = {b.building_id: b.attribution for b in result.summary.buildings}
    assert by_building == {
        "3376": Attribution.THIS_BUILDING,
        "3378": Attribution.OTHER_BUILDING_ON_LOT,
    }
