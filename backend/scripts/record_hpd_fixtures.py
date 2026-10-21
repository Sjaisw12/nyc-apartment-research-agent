"""Record live resolver + HPD violations responses for offline tests (Phase 2, Task 1).

    uv run python scripts/record_hpd_fixtures.py

For each address: run the real resolver, then the real HPD violations retrieval, and
save every HTTP response into one cassette. Before saving, HPD rows are redacted
(apartment, story, unit detail in novdescription; see records/redaction.py). Each
redacted entry keeps the SHA-256 of the original body, so the redaction is visible.

The unredacted rows are NOT saved here. Nothing in this script writes raw rows to git.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from nyc_apartment_agent.cassette import RecordingTransport, save_cassette
from nyc_apartment_agent.records.hpd_violations import DATASET_ID, retrieve_hpd_violations
from nyc_apartment_agent.records.redaction import redact_row
from nyc_apartment_agent.resolver.resolve import resolve_address
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "hpd"
CASSETTE = FIXTURES / "violations_cassette.json"
META = FIXTURES / "violations_cassette.meta.json"
PAGE_SIZE = 250  # small on purpose, so the 666-row test building needs three pages

ADDRESSES = {
    # key: (address, why it is in the set)
    "west_farms_1134": (
        "1134 West Farms Rd, Bronx, NY 10459",
        "Project test building; building-level resolution; 600+ rows over several pages.",
    ),
    "west_17_220": (
        "220 West 17th Street, Manhattan",
        "Lot 1007660055 holds 8 buildings; rows on other BINs and rows with no BBL/BIN.",
    ),
    "west_farms_1217": (
        "1217 West Farms Road, Bronx",
        "Tax-lot-level resolution (PAD gives placeholder BIN 2000000).",
    ),
    "fifth_ave_350": (
        "350 5th Avenue, New York, NY 10118",
        "Empire State Building; building-level resolution; zero HPD violations.",
    ),
    "acp_1845": (
        "1845 Adam Clayton Powell Jr Blvd, Manhattan",
        "Condo: HPD files these violations under billing lot 7502.",
    ),
}


def redact_entries(entries: dict[str, dict]) -> dict[str, int]:
    """Redact HPD violation rows in place. Returns {url: rows redacted}."""
    report: dict[str, int] = {}
    for url, entry in entries.items():
        if f"/resource/{DATASET_ID}.json" not in url or "count%28%2A%29" in url:
            continue
        rows = json.loads(entry["body"])
        if not isinstance(rows, list):
            continue
        original_sha = hashlib.sha256(entry["body"].encode("utf-8")).hexdigest()
        redacted, touched = [], 0
        for row in rows:
            new, fields = redact_row(row)
            touched += bool(fields)
            redacted.append(new)
        entry["body"] = json.dumps(redacted, ensure_ascii=False)
        entry["original_body_sha256"] = original_sha
        entry["redaction"] = "apartment, story -> [REDACTED]; unit/floor detail in novdescription"
        report[url] = touched
    return report


def main() -> None:
    geo_rec, soc_rec = RecordingTransport(), RecordingTransport()
    summary: dict[str, dict] = {}
    with (
        geosearch_client(transport=geo_rec) as geo,
        socrata_client(transport=soc_rec) as soc,
    ):
        for key, (address, why) in ADDRESSES.items():
            resolution = resolve_address(address, geo, soc)
            result = retrieve_hpd_violations(resolution, soc, page_size=PAGE_SIZE)
            summary[key] = {
                "address": address,
                "why": why,
                "outcome": resolution.outcome.value,
                "resolution_level": getattr(resolution.resolution_level, "value", None),
                "bbl": resolution.building.bbl if resolution.building else None,
                "bin": resolution.building.bin if resolution.building else None,
                "status": result.status.value,
                "source_count": result.provenance.source_count,
                "records": len(result.records),
                "data_as_of": result.provenance.data_as_of,
            }
            print(json.dumps(summary[key]))

    entries = {**geo_rec.entries, **soc_rec.entries}
    redacted = redact_entries(entries)
    save_cassette(CASSETTE, entries)
    META.write_text(
        json.dumps(
            {
                "recorded_at": datetime.now(UTC).isoformat(),
                "page_size": PAGE_SIZE,
                "addresses": summary,
                "redacted_rows_per_url": redacted,
                "note": "HPD rows are redacted before saving; original body hashes are kept "
                "per entry as original_body_sha256. Unredacted rows are not in git.",
            },
            indent=1,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"saved {len(entries)} responses to {CASSETTE}")


if __name__ == "__main__":
    main()
