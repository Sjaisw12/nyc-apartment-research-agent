"""Live checks for Task 5 step 4 (311 Service Requests). Skipped unless RUN_LIVE_TESTS=1.

Counts change daily and the source alternates between two data versions (design 18.4),
so these check invariants rather than exact numbers or a fixed status.
"""

import os

import pytest

from nyc_apartment_agent.records.models import Attribution, JoinScope, RetrievalStatus
from nyc_apartment_agent.records.redaction import K311_PATTERN
from nyc_apartment_agent.records.sr311 import (
    DATASET_ID,
    PUBLIC_SOURCE_FIELDS,
    REQUIRED_FIELDS,
    retrieve_service_requests,
)
from nyc_apartment_agent.resolver import resolve_address
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1", reason="set RUN_LIVE_TESTS=1"),
]
KEY = b"live-test-key"


def run(address):
    with geosearch_client() as g, socrata_client() as s:
        resolution = resolve_address(address, g, s)
        return retrieve_service_requests(resolution, s, digest_key=KEY)


def test_schema_header_still_has_every_field_we_use():
    with socrata_client() as s:
        resp = s.get_json(f"/resource/{DATASET_ID}.json", params={"$limit": 1})
    fields = resp.headers["x-soda2-fields"]
    for f in set(REQUIRED_FIELDS) | PUBLIC_SOURCE_FIELDS:
        assert f'"{f}"' in fields


def _check(result):
    assert result.status in (RetrievalStatus.RECORDS_FOUND, RetrievalStatus.INCOMPLETE_PAGINATION)
    assert result.records
    for r in result.records:
        assert K311_PATTERN.match(r.request_ref)
        for d in (r.received_date, r.closed_date, r.agency_updated_date):
            assert d.raw is None or len(d.raw) == 10
    assert result.attempts and len(result.attempts) <= 3
    by = result.excluded_counts.by_agency
    if result.status is RetrievalStatus.RECORDS_FOUND:
        assert result.accepted_data_version == result.provenance.data_as_of
        assert by.established is (by.ok and by.truth_header == result.accepted_data_version)
    else:
        assert not by.established and by.not_established_reason


def test_building_scope_is_never_attributed_to_a_building():
    result = run("350 5th Avenue, Manhattan")
    _check(result)
    assert result.join_scope is JoinScope.BUILDING
    assert {r.attribution for r in result.records} == {Attribution.UNATTRIBUTED_ON_LOT}
    assert "agency not in ('HPD', 'DOB')" in result.provenance.where


def test_tax_lot_is_lot_level():
    result = run("1217 West Farms Road, Bronx")
    _check(result)
    assert result.join_scope is JoinScope.TAX_LOT
    assert {r.attribution for r in result.records} == {Attribution.LOT_LEVEL}


def test_condo_lot_family_is_queried():
    result = run("1845 Adam Clayton Powell Jr Blvd, Manhattan")
    _check(result)
    assert result.lot_family == ["1018220001", "1018227502"]
