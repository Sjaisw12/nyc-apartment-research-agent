"""Task 5 step 5, decision A: measure 311 vocabularies over 2020 to present (grouped counts).

    uv run python scripts/task5_measure_311_history.py OUT.json

Read-only, aggregates only. For each public picklist or template field of 311 Service
Requests (erm2-nwe9), among rows with an agency other than HPD or DOB, it asks for the
count of every value, one calendar year at a time from 2020 (bounded queries over about
3 million rows each, instead of one query over 22 million that times out). Values are
summed across years and the step-4 rule is applied: at least 20 rows.

Version-aware: every year of a field must carry the same X-SODA2-Truth-Last-Modified
header. A year on another version is asked again (up to three times); if it never
matches, the field is marked `unstable` and is not used. A year whose answer reaches the
`$limit`, or fails, marks the field `partial` and it is not used. Every query is logged
with its header and time.

The output holds raw grouped values, which have NOT been reviewed for private text. It is
written outside the repository and is never committed. Only the reviewed result
(scripts/task5_review_311_history.py -> docs/task5/sr311_history_review.json) is.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.errors import SourceError
from nyc_apartment_agent.sources.clients import socrata_client

DATASET = "erm2-nwe9"
AGENCY = "agency not in ('HPD', 'DOB')"
YEARS = tuple(range(2020, 2027))
MIN_COUNT = 20
LIMIT = 50000
FIELDS = (
    "agency",
    "complaint_type",
    "descriptor",
    "descriptor_2",
    "location_type",
    "resolution_description",
)
TRUTH = "x-soda2-truth-last-modified"
MAX_TRIES = 3


def year_where(year: int) -> str:
    return (
        f"created_date >= '{year}-01-01T00:00:00' AND created_date < '{year + 1}-01-01T00:00:00'"
        f" AND {AGENCY}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    out_path = ap.parse_args().out
    if Path(__file__).resolve().parents[2] in out_path.resolve().parents:
        raise SystemExit("write the raw measurement outside the repository")
    log: list[dict] = []
    fields: dict[str, dict] = {}
    settings = Settings(http_read_timeout_seconds=180)
    with socrata_client(settings) as soc:

        def run(field: str, year: int) -> tuple[list[dict] | None, str | None]:
            params = {
                "$select": f"{field}, count(*) AS n",
                "$where": year_where(year),
                "$group": field,
                "$limit": LIMIT,
            }
            t0 = time.monotonic()
            entry = {"field": field, "year": year, "params": params}
            try:
                resp = soc.get_json(f"/resource/{DATASET}.json", params=params)
            except SourceError as exc:
                entry.update(error=type(exc).__name__, seconds=round(time.monotonic() - t0, 1))
                log.append(entry)
                return None, None
            entry.update(
                truth_header=resp.headers.get(TRUTH),
                rows=len(resp.data),
                seconds=round(time.monotonic() - t0, 1),
                at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
            log.append(entry)
            return resp.data, resp.headers.get(TRUTH)

        for field in FIELDS:
            target: str | None = None
            counts: Counter[str] = Counter()
            null_rows = 0
            status = "complete"
            for year in YEARS:
                for _ in range(MAX_TRIES):
                    rows, header = run(field, year)
                    if rows is None:
                        continue
                    if target is None and header is not None:
                        target = header
                    if header == target:
                        break
                else:
                    status = "unstable" if rows is not None else "partial"
                    break
                if len(rows) >= LIMIT:
                    status = "partial"
                    break
                for r in rows:
                    if r.get(field) is None:
                        null_rows += int(r["n"])
                    else:
                        counts[str(r[field])] += int(r["n"])
            frequent = {v: n for v, n in counts.items() if n >= MIN_COUNT}
            fields[field] = {
                "status": status,
                "truth_header": target,
                "total_rows": sum(counts.values()) + null_rows,
                "null_rows": null_rows,
                "distinct_values": len(counts),
                "values": dict(sorted(frequent.items())) if status == "complete" else {},
                "rows_in_listed_values": sum(frequent.values()),
            }
            print(field, status, len(frequent), target, flush=True)
    out = {
        "dataset": DATASET,
        "where": f"created_date in {YEARS[0]}..{YEARS[-1]} (one query per year) AND {AGENCY}",
        "min_count": MIN_COUNT,
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "unreviewed": True,
        "fields": fields,
        "query_log": log,
    }
    out_path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
