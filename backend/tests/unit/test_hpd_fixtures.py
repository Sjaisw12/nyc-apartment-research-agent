"""HPD responses captured live, plus query-building safety checks."""

import httpx
import pytest

from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.errors import InvalidIdentifierError, SourceResponseError
from nyc_apartment_agent.sources import hpd
from nyc_apartment_agent.sources.clients import socrata_client

BBL = "2027540005"


def test_violations_fixture_matches_schema_and_building(fixture_loader):
    resp = fixture_loader("nyc_open_data", f"hpd_violations_{BBL}_first5")
    assert len(resp.data) == 5
    assert hpd.missing_fields(resp.data, hpd.VIOLATION_FIELDS) == set()
    assert {r["bbl"] for r in resp.data} == {BBL}
    assert "last-modified" in resp.headers


def test_complaint_problems_fixture_matches_schema_and_building(fixture_loader):
    resp = fixture_loader("nyc_open_data", f"hpd_complaint_problems_{BBL}_first5")
    assert hpd.missing_fields(resp.data, hpd.COMPLAINT_PROBLEM_FIELDS) == set()
    assert {r["bbl"] for r in resp.data} == {BBL}


def test_count_fixtures_are_positive_integers(fixture_loader):
    for name in (f"hpd_violations_count_{BBL}", f"hpd_complaint_problems_count_{BBL}"):
        resp = fixture_loader("nyc_open_data", name)
        assert int(resp.data[0]["n"]) > 0


def recording_client(body):
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    return requests, socrata_client(Settings(_env_file=None), transport=transport)


def test_query_is_bounded_ordered_and_filtered_by_bbl():
    requests, client = recording_client([])
    hpd.get_violations(client, BBL, limit=50_000)
    params = requests[0].url.params
    assert params["$where"] == f"bbl='{BBL}'"
    assert params["$order"] == "violationid"
    assert params["$limit"] == str(hpd.MAX_PAGE)
    assert requests[0].url.path == "/resource/wvxf-dwi5.json"


def test_injection_attempt_never_reaches_the_network():
    requests, client = recording_client([])
    with pytest.raises(InvalidIdentifierError):
        hpd.get_violations(client, "2027540005' OR '1'='1")
    assert requests == []


def test_non_list_body_is_rejected():
    _, client = recording_client({"error": True, "message": "query failed"})
    with pytest.raises(SourceResponseError):
        hpd.get_violations(client, BBL)


def test_bad_count_body_is_rejected():
    _, client = recording_client([])
    with pytest.raises(SourceResponseError):
        hpd.count_by_bbl(client, hpd.VIOLATIONS_DATASET, BBL)
