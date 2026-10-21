import httpx

from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client


def capture_headers():
    seen: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return httpx.Response(200, json=[])

    return seen, httpx.MockTransport(handler)


def test_blank_token_means_no_token(monkeypatch):
    monkeypatch.setenv("SOCRATA_APP_TOKEN", "   ")
    assert Settings(_env_file=None).socrata_app_token is None


def test_token_is_sent_to_socrata_only(monkeypatch):
    monkeypatch.setenv("SOCRATA_APP_TOKEN", "test-token-123")
    settings = Settings(_env_file=None)

    seen, transport = capture_headers()
    with socrata_client(settings, transport=transport) as c:
        c.get_json("/resource/x.json")
    assert seen[0]["X-App-Token"] == "test-token-123"

    seen, transport = capture_headers()
    with geosearch_client(settings, transport=transport) as c:
        c.get_json("/v2/search")
    assert "X-App-Token" not in seen[0]


def test_token_is_hidden_in_repr(monkeypatch):
    monkeypatch.setenv("SOCRATA_APP_TOKEN", "test-token-123")
    assert "test-token-123" not in repr(Settings(_env_file=None))


def test_no_token_header_when_unset(monkeypatch):
    monkeypatch.delenv("SOCRATA_APP_TOKEN", raising=False)
    seen, transport = capture_headers()
    with socrata_client(Settings(_env_file=None), transport=transport) as c:
        c.get_json("/resource/x.json")
    assert "X-App-Token" not in seen[0]
