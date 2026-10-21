"""Record real HTTP responses once, then replay them offline (deterministic tests).

A cassette is a JSON file mapping request URL -> recorded status, headers and body,
plus a SHA-256 of the entries so an edited cassette is detected.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

_KEEP_HEADERS = (
    "content-type",
    "last-modified",
    "retry-after",
    # Socrata dataset version, used by the pager's freshness check (Phase 2).
    "x-soda2-truth-last-modified",
    "x-soda2-fields",
    "x-socrata-requestid",
)


def _digest(entries: dict) -> str:
    blob = json.dumps(entries, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class RecordingTransport(httpx.BaseTransport):
    """Passes requests to the real network and remembers every response."""

    def __init__(self, entries: dict | None = None) -> None:
        self._inner = httpx.HTTPTransport()
        self.entries: dict[str, dict] = entries if entries is not None else {}

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._inner.handle_request(request)
        body = response.read()
        self.entries[str(request.url)] = {
            "status": response.status_code,
            "headers": {k: v for k, v in response.headers.items() if k in _KEEP_HEADERS},
            "body": body.decode("utf-8"),
        }
        # The body is already decoded, so drop encoding/length headers before re-wrapping.
        headers = [
            (k, v)
            for k, v in response.headers.items()
            if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
        ]
        return httpx.Response(response.status_code, headers=headers, content=body, request=request)

    def close(self) -> None:
        self._inner.close()


class ReplayTransport(httpx.BaseTransport):
    """Answers only from a cassette. An unrecorded URL behaves like a network failure."""

    def __init__(self, entries: dict[str, dict]) -> None:
        self.entries = entries
        self.misses: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        entry = self.entries.get(str(request.url))
        if entry is None:
            self.misses.append(str(request.url))
            raise httpx.ConnectError(f"not in cassette: {request.url}", request=request)
        return httpx.Response(
            entry["status"],
            headers=entry["headers"],
            content=entry["body"].encode("utf-8"),
            request=request,
        )


def save_cassette(path: Path, entries: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "sha256": _digest(entries),
        "entries": entries,
    }
    path.write_text(
        json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def load_cassette(path: Path) -> dict[str, dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if _digest(payload["entries"]) != payload["sha256"]:
        raise ValueError(f"cassette {path} was modified after recording")
    return payload["entries"]
