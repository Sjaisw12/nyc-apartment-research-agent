"""Build the 311 reference file used by the adapter (Task 5, step 4).

    uv run python scripts/task5_build_311_reference.py

Writes src/nyc_apartment_agent/records/data/sr311_reference.json from committed sources:

- docs/task5/sr311_dictionary_values.json: values listed in the 311 dictionary's
  "All Agencies Complaint<>Details" tab (extracted verbatim, workbook SHA-256 recorded);
- docs/task5/step1_measurements.json: values seen at least 20 times citywide in the
  step-1 reference window (rows created since 2026-09-01, agencies other than HPD and
  DOB), with the window, the query and its X-SODA2-Truth-Last-Modified header.

Allowlist rule (design 18.2): a value is public when it is in the dictionary tab or was
seen at least 20 times in the window, AND it shows no unit, floor, phone, email or
honorific pattern (fail closed). `resolution_description` has no dictionary list: only
the frequency rule applies, and the adapter still runs the unit and floor redactor on it.
The lists are a snapshot. They are not exhaustive and not permanently authoritative: a
value outside them is withheld and flagged, never passed through.

Status mapping (design 4.2, step-1 report 3.3): open for Open, In Progress, Assigned,
Pending, Started; closed for Closed; canceled for Canceled; unknown otherwise. A closed
status is never read as "resolved" or "repaired".
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "task5"
PACKAGE = ROOT / "backend" / "src" / "nyc_apartment_agent"
OUT_FILE = PACKAGE / "records" / "data" / "sr311_reference.json"
MIN_COUNT = 20
FIELDS = ("complaint_type", "descriptor", "descriptor_2", "location_type")
# Same patterns as the redactor, plus phone, email and honorific (as for ECB texts).
_UNIT_FLOOR = re.compile(
    r"\b(?:APTS?|APARTMENTS?|UNITS?)\s*(?:NO\.?|#)?\s*\d|\b(?:FLOOR|FLR|FL)\.?\s*#?\d"
    r"|\b\d{1,3}(?:ST|ND|RD|TH)\s+(?:FLOOR|FLR|FL)\b",
    re.IGNORECASE,
)
_PRIVATE = re.compile(r"@|\d{3}[-. )]+\d{3}[-. ]\d{4}|\b(?:MR|MRS|MS|MISS|DR)\.?\s+[A-Z]")
STATUS = {
    "Open": "open",
    "In Progress": "open",
    "Assigned": "open",
    "Pending": "open",
    "Started": "open",
    "Closed": "closed",
    "Canceled": "canceled",
}
STATUS_NOTE = (
    "The status is the agency's record of the request. Closed means the agency closed the "
    "request; it does not show that a condition was corrected or repaired."
)


def _private(value: str) -> bool:
    return bool(_UNIT_FLOOR.search(value) or _PRIVATE.search(value))


def _query(step1: dict, label: str) -> dict:
    q = next(x for x in step1["query_log"] if x["label"] == label)
    return {"where": q["params"]["$where"], "truth_header": q["truth_last_modified"], "at": q["at"]}


def build() -> dict:
    dictionary = json.loads((DOCS / "sr311_dictionary_values.json").read_text(encoding="utf-8"))
    step1 = json.loads((DOCS / "step1_measurements.json").read_text(encoding="utf-8"))
    res = step1["results"]
    allow: dict[str, list[str]] = {}
    stats: dict[str, dict] = {}
    for field in (*FIELDS, "resolution_description"):
        vocab = res[f"sr311_{field}_vocabulary"]
        assert vocab["values_listed_min_count"] == MIN_COUNT
        frequent = {v for v, n in vocab["listed"].items() if n >= MIN_COUNT}
        listed = set(dictionary["values"].get(field, []))
        candidates = frequent | listed
        excluded = sorted(v for v in candidates if _private(v))
        allow[field] = sorted(candidates - set(excluded))
        stats[field] = {
            "dictionary_values": len(listed),
            "frequent_values": len(frequent),
            "allowlisted": len(allow[field]),
            "excluded_privacy_pattern": len(excluded),
            "window_distinct_values": vocab["distinct_values"],
            "window_values_below_min_count": vocab["unlisted_values"],
            "window_rows_below_min_count": vocab["unlisted_rows"],
            "source_query": _query(step1, f"311 {field} vocabulary"),
        }
    agencies = {a: n for a, n in res["sr311_agency"].items() if a not in ("HPD", "DOB")}
    return {
        "generated_by": "backend/scripts/task5_build_311_reference.py",
        "rule": (
            f"public if listed in the dictionary tab or seen at least {MIN_COUNT} times in the "
            "reference window, and free of unit, floor, phone, email and honorific patterns; "
            "a snapshot, not exhaustive"
        ),
        "sources": {
            "dictionary": {
                "file": "docs/task5/sr311_dictionary_values.json",
                "source_url": dictionary["source_url"],
                "sheet": dictionary["sheet"],
                "sha256": dictionary["sha256"],
            },
            "frequencies": {
                "file": "docs/task5/step1_measurements.json",
                "window": res["sr311_window"] + ", agency not in ('HPD', 'DOB')",
                "min_count": MIN_COUNT,
                "measured_at": step1["finished_at"],
            },
        },
        "field_stats": stats,
        "allowlists": allow,
        "agencies": sorted(a for a, n in agencies.items() if n >= MIN_COUNT),
        "agency_source_query": _query(step1, "311 agency"),
        "status": STATUS,
        "status_values_measured": sorted(res["sr311_status"]),
        "status_note": STATUS_NOTE,
    }


def main() -> None:
    OUT_FILE.write_text(json.dumps(build(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT_FILE}")


if __name__ == "__main__":
    main()
