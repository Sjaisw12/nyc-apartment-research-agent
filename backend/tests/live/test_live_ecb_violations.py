"""Live checks for Task 5 step 3 (ECB violations). Skipped unless RUN_LIVE_TESTS=1.

Counts change daily, so these check invariants rather than exact numbers.
"""

import os

import pytest

from nyc_apartment_agent.records.ecb_violations import (
    DATASET_ID,
    NEVER_PUBLIC,
    REQUIRED_FIELDS,
    retrieve_ecb_violations,
)
from nyc_apartment_agent.records.models import Attribution, JoinScope, RetrievalStatus
from nyc_apartment_agent.resolver import resolve_address
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1", reason="set RUN_LIVE_TESTS=1"),
]


def run(address, **kw):
    with geosearch_client() as g, socrata_client() as s:
        resolution = resolve_address(address, g, s)
        return resolution, retrieve_ecb_violations(resolution, s, **kw)


def test_schema_header_still_has_every_field_we_use():
    with socrata_client() as s:
        resp = s.get_json(f"/resource/{DATASET_ID}.json", params={"$limit": 1})
    fields = resp.headers["x-soda2-fields"]
    for f in REQUIRED_FIELDS + NEVER_PUBLIC + ("penality_imposed", "certification_status"):
        assert f'"{f}"' in fields


def test_lot_split_across_two_formats_is_found_once():
    _, result = run("350 5th Avenue, Manhattan")
    assert result.status is RetrievalStatus.RECORDS_FOUND
    fmts = result.summary.lot_format_counts
    assert fmts.get("five_digit", 0) > 0 and fmts.get("four_digit", 0) > 0
    assert len({r.isn_dob_bis_extract for r in result.records}) == len(result.records)
    this = [r for r in result.records if r.attribution is Attribution.THIS_BUILDING]
    assert this and all(r.source_bin == result.resolved_bin for r in this)


def test_tax_lot_is_queried_by_lot_only():
    _, result = run("1217 West Farms Road, Bronx")
    assert result.join_scope is JoinScope.TAX_LOT
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert "bin" not in result.provenance.where
    assert {r.attribution for r in result.records} == {Attribution.LOT_LEVEL}
