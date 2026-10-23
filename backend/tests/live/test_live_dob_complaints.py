"""Live checks for Task 5 step 2 (DOB complaints). Skipped unless RUN_LIVE_TESTS=1.

Counts change daily, so these check invariants rather than exact numbers.
"""

import os

import pytest

from nyc_apartment_agent.records.dob_complaints import (
    DATASET_ID,
    REQUIRED_FIELDS,
    retrieve_dob_complaints,
)
from nyc_apartment_agent.records.models import Attribution, RetrievalStatus
from nyc_apartment_agent.resolver import resolve_address
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1", reason="set RUN_LIVE_TESTS=1"),
]


def run(address, **kw):
    with geosearch_client() as g, socrata_client() as s:
        resolution = resolve_address(address, g, s)
        return resolution, retrieve_dob_complaints(resolution, s, **kw)


def test_schema_header_still_has_every_field_we_use():
    with socrata_client() as s:
        resp = s.get_json(f"/resource/{DATASET_ID}.json", params={"$limit": 1})
    fields = resp.headers["x-soda2-fields"]
    for f in REQUIRED_FIELDS + ("disposition_code", "disposition_date", "inspection_date"):
        assert f'"{f}"' in fields


def test_lot_with_eight_buildings_separates_the_requested_building():
    _, result = run("220 West 17th Street, Manhattan")
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert len(result.search_bins) == 8
    this = [r for r in result.records if r.attribution is Attribution.THIS_BUILDING]
    assert this and all(r.source_bin == result.resolved_bin for r in this)


def test_tax_lot_resolution_makes_no_request():
    _, result = run("1217 West Farms Road, Bronx")
    assert result.status is RetrievalStatus.UNSUPPORTED_JOIN
    assert result.provenance.pages == []
