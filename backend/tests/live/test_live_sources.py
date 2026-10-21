"""Live smoke tests against the real NYC APIs.

Skipped unless RUN_LIVE_TESTS=1, so the default test run never depends on the network.
Assertions avoid exact counts, because the data changes daily.
"""

import os

import pytest

from nyc_apartment_agent.identifiers import BinStatus
from nyc_apartment_agent.sources import geosearch, hpd
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1", reason="set RUN_LIVE_TESTS=1"),
]

BBL, BIN = "2027540005", "2006185"


def test_geosearch_resolves_test_building():
    with geosearch_client() as c:
        result = geosearch.search(c, "1134 West Farms Rd Bronx", size=3)
    assert result.response["status_code"] == 200
    top = result.candidates[0]
    assert (top.bbl, top.bin, top.bin_status) == (BBL, BIN, BinStatus.ASSIGNED)


def test_geosearch_nonsense_returns_nothing():
    with geosearch_client() as c:
        assert geosearch.search(c, "zzqx qqzz nowhere").candidates == []


@pytest.mark.parametrize(
    "dataset,fields,getter",
    [
        (hpd.VIOLATIONS_DATASET, hpd.VIOLATION_FIELDS, hpd.get_violations),
        (hpd.COMPLAINT_PROBLEMS_DATASET, hpd.COMPLAINT_PROBLEM_FIELDS, hpd.get_complaint_problems),
    ],
)
def test_hpd_dataset_is_live_and_schema_is_stable(dataset, fields, getter):
    with socrata_client() as c:
        count, count_resp = hpd.count_by_bbl(c, dataset, BBL)
        rows, resp = getter(c, BBL, limit=5)
    assert count_resp.status_code == resp.status_code == 200
    assert count > 0
    assert len(rows) == 5
    assert hpd.missing_fields(rows, fields) == set()
    assert {r["bbl"] for r in rows} == {BBL}
    assert "last-modified" in resp.headers
