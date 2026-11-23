"""Live smoke check of GeoSearch and HPD, saving hashed, timestamped fixtures.

Run from backend/:  uv run python scripts/smoke_sources.py

Every query is small and bounded (a handful of requests in total). Each response body is
saved as canonical JSON with a sidecar .meta.json holding the URL, fetch time, provenance
headers and the SHA-256 of the saved body, so offline tests can prove the fixture is
unchanged. A summary of what passed and failed is written to fixtures/smoke_report.json.
Exit code is 0 only if every check passed.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nyc_apartment_agent.errors import SourceError
from nyc_apartment_agent.http_client import SourceResponse
from nyc_apartment_agent.records.redaction import redact_row
from nyc_apartment_agent.sources import geosearch, hpd
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
TEST_BBL = "2027540005"  # 1134 West Farms Road, Bronx

GEOSEARCH_QUERIES = {
    "west_farms_1134": "1134 West Farms Rd Bronx",
    "east_228_873": "873 East 228 Street Bronx",
    "centre_st_1": "1 Centre St Manhattan",
    "broadway_100_no_borough": "100 Broadway",
    "nonsense": "zzqx qqzz nowhere",
}


def canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def save(name: str, resp: SourceResponse) -> str:
    original = canonical(resp.data)
    data = resp.data
    if isinstance(data, list):
        # Public fixtures never carry apartment/floor detail (Phase 2 decision D2).
        data = [redact_row(r)[0] if isinstance(r, dict) else r for r in data]
    body = canonical(data)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    folder = FIXTURES / resp.source
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(body, encoding="utf-8")
    meta = {
        "source": resp.source,
        "url": resp.url,
        "status_code": resp.status_code,
        "fetched_at": resp.fetched_at,
        "attempts": resp.attempts,
        "elapsed_seconds": resp.elapsed_seconds,
        "headers": resp.headers,
        "body_sha256": digest,
    }
    if body != original:
        meta["original_body_sha256"] = hashlib.sha256(original.encode("utf-8")).hexdigest()
    (folder / f"{name}.meta.json").write_text(canonical(meta), encoding="utf-8")
    return digest


def main() -> int:
    report: dict[str, Any] = {"run_at": datetime.now(UTC).isoformat(), "checks": []}

    def record(name: str, ok: bool, **details: Any) -> None:
        report["checks"].append({"name": name, "ok": ok, **details})
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {details}")

    with geosearch_client() as gs:
        for name, text in GEOSEARCH_QUERIES.items():
            try:
                resp = gs.get_json(geosearch.SEARCH_PATH, params={"text": text, "size": 5})
                candidates = geosearch.parse_features(resp)
                digest = save(name, resp)
                record(
                    f"geosearch:{name}",
                    True,
                    candidates=len(candidates),
                    top=[(c.label, c.bbl, c.bin, c.bin_status.value) for c in candidates[:3]],
                    elapsed_s=resp.elapsed_seconds,
                    sha256=digest[:12],
                )
            except SourceError as exc:
                record(f"geosearch:{name}", False, error=str(exc))

    with socrata_client() as sc:
        for dataset, label, fields, getter in (
            (hpd.VIOLATIONS_DATASET, "hpd_violations", hpd.VIOLATION_FIELDS, hpd.get_violations),
            (
                hpd.COMPLAINT_PROBLEMS_DATASET,
                "hpd_complaint_problems",
                hpd.COMPLAINT_PROBLEM_FIELDS,
                hpd.get_complaint_problems,
            ),
        ):
            try:
                count, count_resp = hpd.count_by_bbl(sc, dataset, TEST_BBL)
                save(f"{label}_count_{TEST_BBL}", count_resp)
                rows, rows_resp = getter(sc, TEST_BBL, limit=5)
                digest = save(f"{label}_{TEST_BBL}_first5", rows_resp)
                missing = hpd.missing_fields(rows, fields)
                wrong_bbl = [r.get("bbl") for r in rows if r.get("bbl") != TEST_BBL]
                record(
                    f"hpd:{label}",
                    count > 0 and len(rows) == min(5, count) and not missing and not wrong_bbl,
                    count=count,
                    rows=len(rows),
                    missing_fields=sorted(missing),
                    rows_with_other_bbl=wrong_bbl,
                    last_modified=rows_resp.headers.get("last-modified"),
                    rate_limit_headers={
                        k: v for k, v in rows_resp.headers.items() if "ratelimit" in k
                    },
                    elapsed_s=rows_resp.elapsed_seconds,
                    sha256=digest[:12],
                )
            except SourceError as exc:
                record(f"hpd:{label}", False, error=str(exc))

    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "smoke_report.json").write_text(canonical(report), encoding="utf-8")
    return 0 if all(c["ok"] for c in report["checks"]) else 1


if __name__ == "__main__":
    sys.exit(main())
