"""Live checks for Phase 2 Task 1 (HPD violations). Skipped unless RUN_LIVE_TESTS=1.

Counts change daily, so these check invariants rather than exact numbers.
"""

import os

import pytest

from nyc_apartment_agent.records.hpd_violations import (
    DATASET_ID,
    REQUIRED_FIELDS,
    retrieve_hpd_violations,
)
from nyc_apartment_agent.records.models import Attribution, QualityFlag, RetrievalStatus
from nyc_apartment_agent.resolver import resolve_address
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1", reason="set RUN_LIVE_TESTS=1"),
]


def run(address, **kw):
    with geosearch_client() as g, socrata_client() as s:
        resolution = resolve_address(address, g, s)
        return resolution, retrieve_hpd_violations(resolution, s, **kw)


def test_schema_header_still_has_every_field_we_use():
    with socrata_client() as s:
        resp = s.get_json(f"/resource/{DATASET_ID}.json", params={"$limit": 1})
    fields = resp.headers["x-soda2-fields"]
    for f in REQUIRED_FIELDS + ("bbl", "bin", "apartment", "story", "ordernumber"):
        assert f'"{f}"' in fields


def test_test_building_is_complete_and_matches_count():
    res, result = run("1134 West Farms Rd, Bronx, NY 10459", page_size=250)
    assert res.permitted_join_keys == ["bbl", "bin"]
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert len(result.records) == result.provenance.source_count >= 600
    assert len(result.provenance.pages) >= 4  # count + at least three pages
    assert result.provenance.data_as_of


def test_missing_bbl_rows_on_multi_building_lot_are_retrieved():
    _, result = run("220 West 17th Street, Manhattan")
    assert result.status is RetrievalStatus.RECORDS_FOUND
    no_bbl = [r for r in result.records if QualityFlag.BBL_MISSING in r.quality_flags]
    assert no_bbl, "expected rows with no BBL on lot 1007660055"
    assert all(r.attribution is Attribution.UNATTRIBUTED_ON_LOT for r in no_bbl)


def test_tax_lot_resolution_stays_lot_level():
    res, result = run("1217 West Farms Road, Bronx")
    assert res.permitted_join_keys == ["bbl"]
    assert {r.attribution for r in result.records} <= {
        Attribution.LOT_LEVEL,
        Attribution.UNATTRIBUTED_ON_LOT,
    }
