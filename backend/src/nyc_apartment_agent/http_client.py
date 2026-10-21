"""One HTTP helper shared by every data source.

What it guarantees:
- Every request has a connect and read timeout.
- Retries are bounded: at most `max_attempts` tries in total.
- Only transient failures are retried: HTTP 429, 500/502/503/504, timeouts, and
  connection errors. Other 4xx responses mean our request is wrong, so they fail at once.
- A server's Retry-After header is respected, but never beyond `max_retry_after`;
  if a source asks for a longer wait we fail fast instead of hanging a user request.
- Failures raise typed SourceError subclasses (see errors.py).
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .errors import (
    SourceClientError,
    SourceConnectionError,
    SourceRateLimitedError,
    SourceResponseError,
    SourceServerError,
    SourceTimeoutError,
)

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Response headers worth keeping as provenance (lower-case names).
PROVENANCE_HEADERS = (
    "date",
    "last-modified",
    "etag",
    "x-soda2-fields",
    "x-soda2-types",
    "x-soda2-truth-last-modified",
    "x-soda2-data-out-of-date",
    "x-socrata-requestid",
    "retry-after",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_base: float = 0.5  # seconds; doubles each retry
    backoff_cap: float = 8.0
    max_retry_after: float = 30.0

    def backoff(self, retry_number: int) -> float:
        """Exponential backoff with full jitter. retry_number starts at 1."""
        ceiling = min(self.backoff_cap, self.backoff_base * (2 ** (retry_number - 1)))
        return random.uniform(0, ceiling)  # noqa: S311 - jitter, not crypto


@dataclass
class SourceResponse:
    source: str
    url: str
    status_code: int
    data: Any
    headers: dict[str, str]
    attempts: int
    elapsed_seconds: float
    fetched_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Retry-After may be seconds ("120") or an HTTP date. Returns seconds, or None."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    now = now or datetime.now(UTC)
    return max(0.0, (when - now).total_seconds())


class SourceHttpClient:
    """Thin wrapper around httpx.Client that applies the rules in the module docstring."""

    def __init__(
        self,
        *,
        source: str,
        base_url: str,
        timeout: httpx.Timeout,
        retry: RetryPolicy,
        headers: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.source = source
        self.retry = retry
        self._sleep = sleep
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers=dict(headers or {}),
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SourceHttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_json(self, path: str, params: Mapping[str, Any] | None = None) -> SourceResponse:
        url = str(self._client.build_request("GET", path, params=params).url)
        last_error: Exception | None = None
        started = time.monotonic()

        for attempt in range(1, self.retry.max_attempts + 1):
            is_last = attempt == self.retry.max_attempts
            try:
                response = self._client.get(path, params=params)
            except httpx.TimeoutException as exc:
                last_error = exc
                logger.warning("%s timeout on attempt %d: %s", self.source, attempt, url)
                if is_last:
                    raise SourceTimeoutError(
                        self.source, url, "request timed out", attempt
                    ) from exc
                self._sleep(self.retry.backoff(attempt))
                continue
            except httpx.TransportError as exc:
                last_error = exc
                logger.warning("%s connection error on attempt %d: %s", self.source, attempt, exc)
                if is_last:
                    raise SourceConnectionError(
                        self.source, url, f"connection failed: {exc}", attempt
                    ) from exc
                self._sleep(self.retry.backoff(attempt))
                continue

            status = response.status_code
            if status in RETRYABLE_STATUS:
                logger.warning("%s HTTP %d on attempt %d: %s", self.source, status, attempt, url)
                wait = self._wait_before_retry(response, attempt)
                if is_last or wait is None:
                    if status == 429:
                        reason = (
                            "rate limited; Retry-After exceeds our limit"
                            if wait is None
                            else "still rate limited (HTTP 429) after all attempts"
                        )
                        raise SourceRateLimitedError(self.source, url, reason, attempt)
                    raise SourceServerError(self.source, url, status, attempt)
                self._sleep(wait)
                continue

            if 400 <= status < 600:
                raise SourceClientError(self.source, url, status, response.text[:300])

            if status != 200:
                # Redirects and odd 2xx codes are unexpected for these read APIs.
                raise SourceResponseError(self.source, url, f"unexpected HTTP {status}", attempt)

            try:
                data = response.json()
            except ValueError as exc:
                raise SourceResponseError(
                    self.source, url, "response body is not valid JSON", attempt
                ) from exc

            kept = {k: response.headers[k] for k in PROVENANCE_HEADERS if k in response.headers}
            return SourceResponse(
                source=self.source,
                url=url,
                status_code=status,
                data=data,
                headers=kept,
                attempts=attempt,
                elapsed_seconds=round(time.monotonic() - started, 3),
            )

        # The loop always returns or raises; this guards against future edits.
        raise SourceConnectionError(
            self.source, url, f"gave up: {last_error}", self.retry.max_attempts
        )

    def _wait_before_retry(self, response: httpx.Response, attempt: int) -> float | None:
        """Seconds to wait before the next try, or None if the server's ask is too long."""
        retry_after = parse_retry_after(response.headers.get("retry-after"))
        if retry_after is None:
            return self.retry.backoff(attempt)
        if retry_after > self.retry.max_retry_after:
            return None
        return retry_after
