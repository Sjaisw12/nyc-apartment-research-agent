"""Live checks for Phase 2 Task 3 (HPD administrative records). Skipped unless
RUN_LIVE_TESTS=1. Invariants only: the data changes monthly."""

import os

import pytest

from nyc_apartment_agent.records.admin_models import RequestedBuildingStatus
from nyc_apartment_agent.records.hpd_buildings import DATASET_ID as BUILDINGS
from nyc_apartment_agent.records.hpd_buildings import REQUIRED_FIELDS as BUILDING_FIELDS
from nyc_apartment_agent.records.hpd_buildings import retrieve_hpd_buildings
from nyc_apartment_agent.records.models import Attribution, RetrievalStatus
from nyc_apartment_agent.resolver import resolve_address
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1", reason="set RUN_LIVE_TESTS=1"),
]


def run(address, fn=retrieve_hpd_buildings, **kw):
    with geosearch_client() as g, socrata_client() as s:
        res = resolve_address(address, g, s)
        return res, fn(res, s, **kw)


def test_buildings_schema_header_has_every_field_used():
    with socrata_client() as s:
        resp = s.get_json(f"/resource/{BUILDINGS}.json", params={"$limit": 1})
    fields = resp.headers["x-soda2-fields"]
    for f in BUILDING_FIELDS + ("bin", "legalclassa", "legalclassb", "registrationid"):
        assert f'"{f}"' in fields


def test_test_building_has_one_current_record_and_freshness():
    _, result = run("1134 West Farms Rd, Bronx, NY 10459")
    assert result.status is RetrievalStatus.RECORDS_FOUND
    rb = result.summary.requested_building
    assert rb.status is RequestedBuildingStatus.ONE_CURRENT_RECORD
    assert result.freshness.data_updated_at and result.freshness.metadata_updated_at
    assert result.freshness.stated_update_frequency


def test_tax_lot_buildings_stay_lot_level():
    _, result = run("1217 West Farms Road, Bronx")
    assert {r.attribution for r in result.records} <= {Attribution.LOT_LEVEL}
