"""Phase 3 API: /config, /investigate and report exports, in demo mode (offline)."""

import pytest
from fastapi.testclient import TestClient

from nyc_apartment_agent.api.app import create_app
from nyc_apartment_agent.config import get_settings


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DATA_MODE", "demo")
    monkeypatch.setenv("LLM_ENABLED", "0")
    monkeypatch.setenv("INVESTIGATE_RATE_PER_MINUTE", "5")
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


def test_config_reports_demo_mode_and_no_llm(client):
    body = client.get("/config").json()
    assert body["data_mode"] == "demo" and body["llm_available"] is False
    assert len(body["examples"]) == 5
    assert "key" not in str(body["llm_detail"]).lower() or "no" in body["llm_detail"]


def test_investigate_returns_verified_report_and_exports(client):
    r = client.post("/investigate", json={"address": "1134 West Farms Rd, Bronx, NY 10459"})
    assert r.status_code == 200
    body = r.json()
    assert body["resolution"]["bin"] == "2006185"
    assert body["verification"]["passed"] is True
    assert body["agent"]["status"] == "disabled"
    rid = body["report_id"]
    md = client.get(f"/reports/{rid}.md")
    assert md.status_code == 200 and md.text.startswith("# NYC Apartment Research Report")
    js = client.get(f"/reports/{rid}.json")
    assert js.status_code == 200 and js.json()["report_id"] == rid
    assert client.get("/reports/nope.md").status_code == 404


def test_ambiguous_then_selected_option(client):
    amb = client.post("/investigate", json={"address": "100 Broadway"}).json()
    assert amb["resolution"]["outcome"] == "ambiguous"
    opt = amb["resolution"]["options"][0]
    chosen = client.post(
        "/investigate",
        json={"address": "100 Broadway", "selected_option": {"bbl": opt["bbl"], "bin": opt["bin"]}},
    ).json()
    assert chosen["resolution"]["outcome"] == "verified"
    assert chosen["resolution"]["user_selected"] is True


def test_invalid_input_is_not_echoed(client):
    r = client.post("/investigate", json={"address": "", "extra": "x"})
    assert r.status_code == 422 and "extra" not in r.json()["message"]
    bad = client.post("/investigate", json={"address": "1 Main", "selected_option": {"bbl": "x"}})
    assert bad.status_code == 422


def test_rate_limit(client):
    codes = [
        client.post("/investigate", json={"address": "zzqx qqzz nowhere"}).status_code
        for _ in range(7)
    ]
    assert codes[:5] == [200] * 5 and codes[-1] == 429


def test_cors_allows_local_frontend(client):
    r = client.options(
        "/investigate",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
