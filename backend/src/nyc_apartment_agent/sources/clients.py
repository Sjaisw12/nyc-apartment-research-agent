"""Factories that build a configured HTTP client per source."""

from __future__ import annotations

import httpx

from ..config import Settings, get_settings
from ..http_client import RetryPolicy, SourceHttpClient


def _timeout(settings: Settings) -> httpx.Timeout:
    return httpx.Timeout(
        settings.http_read_timeout_seconds, connect=settings.http_connect_timeout_seconds
    )


def _retry(settings: Settings) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=settings.http_max_attempts,
        max_retry_after=settings.http_max_retry_after_seconds,
    )


def socrata_client(
    settings: Settings | None = None, transport: httpx.BaseTransport | None = None
) -> SourceHttpClient:
    """Client for data.cityofnewyork.us. Sends the app token only if one is configured."""
    settings = settings or get_settings()
    headers = {"User-Agent": settings.user_agent, "Accept": "application/json"}
    if settings.socrata_app_token is not None:
        headers["X-App-Token"] = settings.socrata_app_token.get_secret_value()
    return SourceHttpClient(
        source="nyc_open_data",
        base_url=settings.socrata_base_url,
        timeout=_timeout(settings),
        retry=_retry(settings),
        headers=headers,
        transport=transport,
    )


def geosearch_client(
    settings: Settings | None = None, transport: httpx.BaseTransport | None = None
) -> SourceHttpClient:
    """Client for NYC Planning GeoSearch. Never receives the Socrata token."""
    settings = settings or get_settings()
    return SourceHttpClient(
        source="geosearch",
        base_url=settings.geosearch_base_url,
        timeout=_timeout(settings),
        retry=_retry(settings),
        headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
        transport=transport,
    )
