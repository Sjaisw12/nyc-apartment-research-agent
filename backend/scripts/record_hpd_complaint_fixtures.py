"""Record live resolver + HPD complaints responses for offline tests (Phase 2, Task 2).

    uv run python scripts/record_hpd_complaint_fixtures.py

Same five addresses as the violations cassette (scripts/record_hpd_fixtures.py), saved to
a separate cassette so the Task 1 fixture stays unchanged. Before saving, every problem
row is sanitized:
- `apartment` -> [REDACTED]; unit/floor detail in `status_description` is redacted;
- `complaint_anonymous_flag` and `unique_key` (311 request number) -> [WITHHELD].
Each sanitized entry keeps the SHA-256 of the original body and per-row redaction events
(field names, kinds and counts only, never the values). Unsanitized rows are not saved.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from record_hpd_fixtures import ADDRESSES

from nyc_apartment_agent.cassette import RecordingTransport, save_cassette
from nyc_apartment_agent.records.hpd_complaints import DATASET_ID, retrieve_hpd_complaints
from nyc_apartment_agent.records.redaction import (
    event_is_empty,
    redact_row_event,
    withhold_fields,
)
from nyc_apartment_agent.resolver.resolve import resolve_address
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "hpd"
CASSETTE = FIXTURES / "complaints_cassette.json"
META = FIXTURES / "complaints_cassette.meta.json"
PAGE_SIZE = 500  # the test building (912 rows) needs two pages, the condo lot six


def sanitize_entries(entries: dict[str, dict]) -> dict[str, int]:
    """Sanitize complaint rows in place. Returns {url: rows changed}."""
    report: dict[str, int] = {}
    for url, entry in entries.items():
        if f"/resource/{DATASET_ID}.json" not in url or "count%28%2A%29" in url:
            continue
        rows = json.loads(entry["body"])
        if not isinstance(rows, list):
            continue
        original_sha = hashlib.sha256(entry["body"].encode("utf-8")).hexdigest()
        out, events = [], []
        for row in rows:
            new, event = redact_row_event(row)
            new, withheld = withhold_fields(new)
            out.append(new)
            if not event_is_empty(event) or withheld:
                events.append({"problem_id": row.get("problem_id"), **event, "withheld": withheld})
        entry["body"] = json.dumps(out, ensure_ascii=False)
        entry["original_body_sha256"] = original_sha
        entry["redaction_events"] = events
        entry["redaction"] = (
            "apartment -> [REDACTED]; unit/floor detail in status_description; "
            "complaint_anonymous_flag, unique_key -> [WITHHELD]"
        )
        report[url] = len(events)
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
            result = retrieve_hpd_complaints(resolution, soc, page_size=PAGE_SIZE)
            summary[key] = {
                "address": address,
                "why": why,
                "outcome": resolution.outcome.value,
                "resolution_level": getattr(resolution.resolution_level, "value", None),
                "bbl": resolution.building.bbl if resolution.building else None,
                "bin": resolution.building.bin if resolution.building else None,
                "status": result.status.value,
                "source_count": result.provenance.source_count,
                "problems": len(result.records),
                "complaints": len(result.complaints),
                "data_as_of": result.provenance.data_as_of,
            }
            print(json.dumps(summary[key]))

    entries = {**geo_rec.entries, **soc_rec.entries}
    changed = sanitize_entries(entries)
    save_cassette(CASSETTE, entries)
    META.write_text(
        json.dumps(
            {
                "recorded_at": datetime.now(UTC).isoformat(),
                "page_size": PAGE_SIZE,
                "addresses": summary,
                "sanitized_rows_per_url": changed,
                "note": "Complaint rows are sanitized before saving (see the script docstring); "
                "original body hashes are kept per entry. Unsanitized rows are not in git.",
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
