"""Retry, timeout and error-handling behaviour, tested with a fake transport (no network)."""

import httpx
import pytest

from nyc_apartment_agent.errors import (
    SourceClientError,
    SourceConnectionError,
    SourceRateLimitedError,
    SourceResponseError,
    SourceServerError,
    SourceTimeoutError,
)
from nyc_apartment_agent.http_client import RetryPolicy, SourceHttpClient, parse_retry_after


class Script:
    """A fake server that replays a list of responses or exceptions, one per request."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def make_client(script, max_attempts=3, max_retry_after=30.0):
    sleeps: list[float] = []
    client = SourceHttpClient(
        source="test",
        base_url="https://example.test",
        timeout=httpx.Timeout(1.0),
        retry=RetryPolicy(max_attempts=max_attempts, max_retry_after=max_retry_after),
        transport=httpx.MockTransport(script),
        sleep=sleeps.append,
    )
    return client, sleeps


def ok(body=None, headers=None):
    return httpx.Response(200, json=body if body is not None else [{"a": 1}], headers=headers)


def test_success_first_try_keeps_provenance_headers():
    script = Script(ok(headers={"Last-Modified": "Tue, 20 Oct 2026 14:55:26 GMT", "X-Other": "x"}))
    client, sleeps = make_client(script)
    resp = client.get_json("/r.json", params={"$limit": 1})
    assert resp.data == [{"a": 1}]
    assert resp.attempts == 1
    assert resp.headers == {"last-modified": "Tue, 20 Oct 2026 14:55:26 GMT"}
    assert "%24limit=1" in resp.url
    assert sleeps == []


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_status_is_retried_then_succeeds(status):
    script = Script(httpx.Response(status), ok())
    client, sleeps = make_client(script)
    resp = client.get_json("/r.json")
    assert resp.attempts == 2
    assert script.calls == 2
    assert len(sleeps) == 1


def test_retry_after_seconds_is_honoured():
    script = Script(httpx.Response(429, headers={"Retry-After": "7"}), ok())
    client, sleeps = make_client(script)
    client.get_json("/r.json")
    assert sleeps == [7.0]


def test_retry_after_too_long_fails_fast_without_sleeping():
    script = Script(httpx.Response(429, headers={"Retry-After": "3600"}), ok())
    client, sleeps = make_client(script, max_retry_after=30)
    with pytest.raises(SourceRateLimitedError, match="exceeds"):
        client.get_json("/r.json")
    assert script.calls == 1
    assert sleeps == []


def test_persistent_429_stops_after_max_attempts():
    script = Script(*[httpx.Response(429) for _ in range(3)])
    client, sleeps = make_client(script, max_attempts=3)
    with pytest.raises(SourceRateLimitedError) as info:
        client.get_json("/r.json")
    assert info.value.attempts == 3
    assert script.calls == 3
    assert len(sleeps) == 2  # no sleep after the final attempt


def test_persistent_503_raises_server_error():
    script = Script(*[httpx.Response(503) for _ in range(3)])
    client, _ = make_client(script)
    with pytest.raises(SourceServerError) as info:
        client.get_json("/r.json")
    assert info.value.status_code == 503


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_not_retried(status):
    script = Script(httpx.Response(status, text="bad query"), ok())
    client, sleeps = make_client(script)
    with pytest.raises(SourceClientError) as info:
        client.get_json("/r.json")
    assert info.value.status_code == status
    assert script.calls == 1
    assert sleeps == []


def test_timeout_is_retried_then_raises_typed_error():
    script = Script(*[httpx.ReadTimeout("slow") for _ in range(3)])
    client, sleeps = make_client(script)
    with pytest.raises(SourceTimeoutError):
        client.get_json("/r.json")
    assert script.calls == 3
    assert len(sleeps) == 2


def test_connection_error_recovers():
    script = Script(httpx.ConnectError("refused"), ok())
    client, _ = make_client(script)
    assert client.get_json("/r.json").attempts == 2


def test_connection_error_exhausted():
    script = Script(*[httpx.ConnectError("refused") for _ in range(2)])
    client, _ = make_client(script, max_attempts=2)
    with pytest.raises(SourceConnectionError):
        client.get_json("/r.json")


def test_invalid_json_raises_response_error():
    script = Script(httpx.Response(200, text="<html>maintenance</html>"))
    client, _ = make_client(script)
    with pytest.raises(SourceResponseError, match="not valid JSON"):
        client.get_json("/r.json")


def test_redirect_is_not_followed():
    script = Script(httpx.Response(302, headers={"Location": "https://elsewhere.test"}))
    client, _ = make_client(script)
    with pytest.raises(SourceResponseError, match="unexpected HTTP 302"):
        client.get_json("/r.json")


def test_single_attempt_policy_never_retries():
    script = Script(httpx.Response(503), ok())
    client, _ = make_client(script, max_attempts=1)
    with pytest.raises(SourceServerError):
        client.get_json("/r.json")
    assert script.calls == 1


def test_backoff_is_bounded():
    policy = RetryPolicy(backoff_base=0.5, backoff_cap=8.0)
    for n in range(1, 20):
        assert 0 <= policy.backoff(n) <= 8.0


def test_parse_retry_after_forms():
    from datetime import UTC, datetime

    now = datetime(2026, 10, 21, 6, 0, 0, tzinfo=UTC)
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after("Wed, 21 Oct 2026 06:00:30 GMT", now=now) == 30.0
    assert parse_retry_after("Wed, 21 Oct 2026 05:00:00 GMT", now=now) == 0.0
    assert parse_retry_after("soon") is None
    assert parse_retry_after(None) is None
