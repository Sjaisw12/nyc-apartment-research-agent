"""API tests: real recorded responses replayed through the FastAPI app (no network)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nyc_apartment_agent.api import create_app
from nyc_apartment_agent.cassette import ReplayTransport, load_cassette
from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

CASSETTE = Path(__file__).resolve().parents[1] / "fixtures" / "benchmark" / "resolver_cassette.json"


def make_client(entries) -> TestClient:
    def clients():
        transport = ReplayTransport(entries)
        settings = Settings(_env_file=None)
        gs, sc = geosearch_client(settings, transport), socrata_client(settings, transport)
        try:
            yield gs, sc
        finally:
            gs.close()
            sc.close()

    return TestClient(create_app(clients), raise_server_exceptions=False)


@pytest.fixture(scope="module")
def api():
    return make_client(load_cassette(CASSETTE))


def test_health(api):
    r = api.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.headers["X-Request-ID"]


def test_verified_building(api):
    r = api.post("/resolve", json={"address": "1134 West Farms Rd, Bronx, NY 10459"})
    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "verified"
    assert body["resolution_level"] == "building"
    assert (body["building"]["bbl"], body["building"]["bin"]) == ("2027540005", "2006185")
    assert body["building"]["bin_evidence"] == "cross_verified"
    assert body["permitted_join_keys"] == ["bbl", "bin"]
    assert body["building_specific_findings_allowed"] is True
    assert body["explanation"].startswith("Accepted")
    urls = [q["url"] for q in body["provenance"]["requests"]]
    assert any("geosearch" in u for u in urls) and any("5zhs-2jue" in u for u in urls)
    assert body["provenance"]["request_id"] == r.headers["X-Request-ID"]


def test_tax_lot_level_restricts_join_keys(api):
    r = api.post("/resolve", json={"address": "1217 West Farms Road, Bronx"})
    body = r.json()
    assert r.status_code == 200
    assert body["outcome"] == "verified"
    assert body["resolution_level"] == "tax_lot"
    assert body["building"]["bin"] is None
    assert body["permitted_join_keys"] == ["bbl"]
    assert body["building_specific_findings_allowed"] is False


def test_ambiguous_returns_options(api):
    r = api.post("/resolve", json={"address": "100 Broadway"})
    body = r.json()
    assert r.status_code == 200
    assert body["outcome"] == "ambiguous"
    assert {o["bbl"] for o in body["options"]} == {"1000460003", "3021310001"}
    assert body["permitted_join_keys"] == []


def test_false_match_is_rejected(api):
    r = api.post("/resolve", json={"address": "99999 Broadway, New York, NY"})
    body = r.json()
    assert r.status_code == 200
    assert body["outcome"] == "no_reliable_match"
    assert "HOUSE_NUMBER_MISMATCH" in body["reasons"]


def test_source_unavailable_is_503_with_typed_body():
    api = make_client({})  # nothing recorded: every source call fails
    r = api.post("/resolve", json={"address": "1134 West Farms Rd, Bronx"})
    assert r.status_code == 503
    assert r.headers["Retry-After"] == "30"
    body = r.json()
    assert body["outcome"] == "source_unavailable"
    assert body["outcome"] != "no_reliable_match"
    assert "GEOSEARCH_UNAVAILABLE" in body["reasons"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"address": ""},
        {"address": "    "},
        {"address": "x" * 201},
        {"address": 12345},
        {"address": None},
        {"address": "350 5th Ave, Manhattan", "admin": True},
        ["350 5th Ave"],
    ],
)
def test_invalid_requests_are_422_without_echo(api, payload):
    r = api.post("/resolve", json=payload)
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "invalid_request"
    assert body["request_id"] == r.headers["X-Request-ID"]
    assert "xxxxxxxxxx" not in r.text  # submitted values are not echoed


def test_non_json_body_is_422(api):
    r = api.post("/resolve", content=b"not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_wrong_method_is_405(api):
    assert api.get("/resolve").status_code == 405


def test_unexpected_error_is_500_without_internals():
    def broken_clients():
        raise RuntimeError("secret internal detail")
        yield  # pragma: no cover

    api = TestClient(create_app(broken_clients), raise_server_exceptions=False)
    r = api.post("/resolve", json={"address": "350 5th Ave, Manhattan"})
    assert r.status_code == 500
    assert r.json()["error"] == "internal_error"
    assert "secret internal detail" not in r.text
    assert "Traceback" not in r.text


def test_openapi_documents_all_four_outcomes(api):
    schema = api.get("/openapi.json").json()
    outcome = schema["components"]["schemas"]["Outcome"]["enum"]
    assert set(outcome) == {"verified", "ambiguous", "no_reliable_match", "source_unavailable"}
