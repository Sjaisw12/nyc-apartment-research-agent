"""POST /corrections: stored for review, redacted, no accounts or IPs."""

import json

from fastapi.testclient import TestClient

from nyc_apartment_agent.api.app import create_app
from nyc_apartment_agent.config import get_settings


def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CORRECTIONS_PATH", str(tmp_path / "c.jsonl"))
    get_settings.cache_clear()
    return TestClient(create_app())


def test_correction_is_stored_redacted_and_without_ip(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    r = c.post(
        "/corrections",
        json={
            "kind": "record_incorrect",
            "address": "1134 West Farms Rd, Bronx",
            "report_id": "abc123def456",
            "record_id": "hpd_violation:1",
            "details": "Fixed in APT 4B on the 3RD FLOOR, call me at 212-555-0101 or a@b.com",
        },
    )
    assert r.status_code == 202 and r.json()["status"] == "received"
    entry = json.loads((tmp_path / "c.jsonl").read_text().strip())
    text = json.dumps(entry)
    assert "4B" not in text and "212-555-0101" not in text and "a@b.com" not in text
    assert "testclient" not in text and "ip" not in entry
    get_settings.cache_clear()


def test_correction_rejects_unknown_fields_and_bad_kinds(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    base = {"kind": "other", "address": "1 Centre St", "details": "something is wrong"}
    assert c.post("/corrections", json={**base, "email": "x@y.z"}).status_code == 422
    assert c.post("/corrections", json={**base, "kind": "rank_landlord"}).status_code == 422
    get_settings.cache_clear()
