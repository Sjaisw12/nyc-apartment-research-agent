"""Live checks for Phase 2 Task 2 (HPD complaints). Skipped unless RUN_LIVE_TESTS=1.

Counts change daily, so these check invariants rather than exact numbers.
"""

import os

import pytest

from nyc_apartment_agent.records.complaint_models import ComplaintAttribution
from nyc_apartment_agent.records.hpd_complaints import (
    DATASET_ID,
    REQUIRED_FIELDS,
    retrieve_hpd_complaints,
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
        return resolution, retrieve_hpd_complaints(resolution, s, **kw)


def test_schema_header_still_has_every_field_we_use():
    with socrata_client() as s:
        resp = s.get_json(f"/resource/{DATASET_ID}.json", params={"$limit": 1})
    fields = resp.headers["x-soda2-fields"]
    extra = ("bbl", "bin", "apartment", "status_description", "problem_duplicate_flag")
    for f in REQUIRED_FIELDS + extra + ("complaint_anonymous_flag", "unique_key"):
        assert f'"{f}"' in fields


def test_test_building_complete_with_separate_complaint_count():
    res, result = run("1134 West Farms Rd, Bronx, NY 10459", page_size=500)
    assert res.permitted_join_keys == ["bbl", "bin"]
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert len(result.records) == result.provenance.source_count >= 900
    assert 0 < len(result.complaints) < len(result.records)
    assert result.summary.total_complaints == len(result.complaints)


def test_rows_without_bbl_are_found_by_borough_name_and_kept_unattributed():
    _, result = run("220 West 17th Street, Manhattan")
    no_bbl = [r for r in result.records if QualityFlag.BBL_MISSING in r.quality_flags]
    assert no_bbl, "expected rows with no BBL on lot 1007660055"
    assert all(r.attribution is Attribution.UNATTRIBUTED_ON_LOT for r in no_bbl)


def test_tax_lot_resolution_never_yields_building_claims():
    _, result = run("1217 West Farms Road, Bronx")
    assert {r.attribution for r in result.records} <= {Attribution.LOT_LEVEL}
    assert {c.attribution for c in result.complaints} <= {ComplaintAttribution.LOT_LEVEL}


def test_live_output_carries_no_withheld_values():
    _, result = run("1845 Adam Clayton Powell Jr Blvd, Manhattan", page_size=1000)
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert result.summary.withheld_field_counts.get("unique_key", 0) > 0
    record_fields = set(type(result.records[0]).model_fields)
    assert not record_fields & {"apartment", "unique_key", "complaint_anonymous_flag"}
    c = result.summary.classification
    assert c.classified + c.other == c.problems_with_text
