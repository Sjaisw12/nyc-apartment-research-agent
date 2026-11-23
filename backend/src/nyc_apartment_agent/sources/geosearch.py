"""NYC Planning GeoSearch (https://geosearch.planninglabs.nyc/docs/).

This module only fetches and validates candidates. Choosing the right building and
asking the user to confirm belongs to the resolver (next Phase 1 task).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ..errors import InvalidIdentifierError, SourceResponseError
from ..http_client import SourceHttpClient, SourceResponse
from ..identifiers import BinStatus, classify_bin, normalize_bbl

SEARCH_PATH = "/v2/search"
MAX_RESULTS = 10


class GeoSearchCandidate(BaseModel):
    label: str
    housenumber: str | None
    street: str | None
    borough: str | None
    postalcode: str | None
    bbl: str | None
    bin: str | None
    bin_status: BinStatus
    pad_version: str | None
    match_type: str | None
    confidence: float | None
    longitude: float
    latitude: float
    warnings: list[str] = []


class GeoSearchResult(BaseModel):
    query: str
    candidates: list[GeoSearchCandidate]
    response: dict[str, Any]  # provenance: url, status, headers, fetched_at, attempts


def search(client: SourceHttpClient, text: str, size: int = 5) -> GeoSearchResult:
    """Search GeoSearch for an address and return validated candidates."""
    text = text.strip()
    if not text:
        raise ValueError("address text is empty")
    if len(text) > 200:
        raise ValueError("address text is too long (max 200 characters)")
    size = max(1, min(size, MAX_RESULTS))

    resp = client.get_json(SEARCH_PATH, params={"text": text, "size": size})
    candidates = parse_features(resp)
    return GeoSearchResult(
        query=text,
        candidates=candidates,
        response={
            "url": resp.url,
            "status_code": resp.status_code,
            "headers": resp.headers,
            "fetched_at": resp.fetched_at,
            "attempts": resp.attempts,
            "elapsed_seconds": resp.elapsed_seconds,
        },
    )


def parse_features(resp: SourceResponse) -> list[GeoSearchCandidate]:
    data = resp.data
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise SourceResponseError(resp.source, resp.url, "expected a GeoJSON FeatureCollection")
    features = data.get("features")
    if not isinstance(features, list):
        raise SourceResponseError(resp.source, resp.url, "'features' is missing or not a list")
    return [_parse_feature(resp, f) for f in features]


def _parse_feature(resp: SourceResponse, feature: Any) -> GeoSearchCandidate:
    try:
        props = feature["properties"]
        lon, lat = feature["geometry"]["coordinates"][:2]
        label = props["label"]
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceResponseError(resp.source, resp.url, f"malformed feature: {exc!r}") from exc

    warnings: list[str] = []
    pad = (props.get("addendum") or {}).get("pad") or {}

    bbl: str | None = None
    raw_bbl = pad.get("bbl")
    if raw_bbl:
        try:
            bbl = normalize_bbl(raw_bbl)
        except InvalidIdentifierError:
            warnings.append(f"invalid BBL from source: {raw_bbl!r}")
    else:
        warnings.append("no BBL in result")

    bin_value: str | None = None
    raw_bin = pad.get("bin")
    try:
        bin_status = classify_bin(raw_bin)
        bin_value = str(raw_bin).strip() if bin_status is not BinStatus.MISSING else None
    except InvalidIdentifierError:
        bin_status = BinStatus.MISSING
        warnings.append(f"invalid BIN from source: {raw_bin!r}")
    if bin_status is BinStatus.UNASSIGNED_PLACEHOLDER:
        warnings.append("BIN is a borough placeholder (unassigned/unknown); do not join on it")
    if bbl and bin_value and bbl[0] != bin_value[0]:
        warnings.append("BBL and BIN borough digits disagree")

    confidence = props.get("confidence")
    return GeoSearchCandidate(
        label=label,
        housenumber=props.get("housenumber"),
        street=props.get("street"),
        borough=props.get("borough"),
        postalcode=props.get("postalcode"),
        bbl=bbl,
        bin=bin_value,
        bin_status=bin_status,
        pad_version=pad.get("version"),
        match_type=props.get("match_type"),
        confidence=float(confidence) if confidence is not None else None,
        longitude=float(lon),
        latitude=float(lat),
        warnings=warnings,
    )
