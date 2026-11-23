"""Record live resolver + HPD administrative-record responses (Phase 2, Task 3).

    uv run python scripts/record_hpd_admin_fixtures.py buildings

One cassette per step, so each step's fixture stays fixed once reviewed:
- buildings     -> buildings_cassette.json (kj4p-ruqc + catalog entry)
- registrations -> registrations_cassette.json (kj4p-ruqc, tesw-yqqr + catalog entries)
- contacts      -> contacts_cassette.json (all three datasets; contacts SANITIZED)

Contact rows are sanitized before saving (approved D11): person names and titles,
street, house number and suite are replaced by [WITHHELD] for every contact, and city,
state and zip too for contacts naming a person. Each page keeps the SHA-256 of its
original body and per-row events (field names only, never values).

Same five addresses as Tasks 1 and 2. The buildings and registrations datasets hold no
personal fields, so their rows are saved as received; the fixture privacy scan still
runs over them.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from record_hpd_fixtures import ADDRESSES

from nyc_apartment_agent.cassette import RecordingTransport, save_cassette
from nyc_apartment_agent.records.hpd_buildings import retrieve_hpd_buildings
from nyc_apartment_agent.records.hpd_contacts import DATASET_ID as CONTACTS
from nyc_apartment_agent.records.hpd_contacts import retrieve_hpd_contacts
from nyc_apartment_agent.records.hpd_registrations import retrieve_hpd_registrations
from nyc_apartment_agent.records.redaction import sanitize_contact
from nyc_apartment_agent.resolver.resolve import resolve_address
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "hpd"
PAGE_SIZE = 5  # small on purpose: the 8-building lot needs two pages


def record_buildings() -> None:
    geo_rec, soc_rec = RecordingTransport(), RecordingTransport()
    summary: dict[str, dict] = {}
    with geosearch_client(transport=geo_rec) as geo, socrata_client(transport=soc_rec) as soc:
        for key, (address, why) in ADDRESSES.items():
            res = resolve_address(address, geo, soc)
            result = retrieve_hpd_buildings(res, soc, page_size=PAGE_SIZE)
            summary[key] = {
                "address": address,
                "why": why,
                "resolution_level": getattr(res.resolution_level, "value", None),
                "bbl": res.building.bbl if res.building else None,
                "bin": res.building.bin if res.building else None,
                "status": result.status.value,
                "source_count": result.provenance.source_count,
                "records": len(result.records),
                "data_as_of": result.provenance.data_as_of,
            }
            print(json.dumps(summary[key]))
    entries = {**geo_rec.entries, **soc_rec.entries}
    save_cassette(FIXTURES / "buildings_cassette.json", entries)
    (FIXTURES / "buildings_cassette.meta.json").write_text(
        json.dumps(
            {
                "recorded_at": datetime.now(UTC).isoformat(),
                "page_size": PAGE_SIZE,
                "addresses": summary,
                "note": "kj4p-ruqc has no personal fields; rows saved as received.",
            },
            indent=1,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"saved {len(entries)} responses")


def record_registrations() -> None:
    geo_rec, soc_rec = RecordingTransport(), RecordingTransport()
    summary: dict[str, dict] = {}
    with geosearch_client(transport=geo_rec) as geo, socrata_client(transport=soc_rec) as soc:
        for key, (address, why) in ADDRESSES.items():
            res = resolve_address(address, geo, soc)
            buildings = retrieve_hpd_buildings(res, soc, page_size=PAGE_SIZE)
            result = retrieve_hpd_registrations(res, soc, buildings=buildings, page_size=PAGE_SIZE)
            summary[key] = {
                "address": address,
                "why": why,
                "resolution_level": getattr(res.resolution_level, "value", None),
                "buildings_status": buildings.status.value,
                "status": result.status.value,
                "source_count": result.provenance.source_count,
                "rows": len(result.records),
                "data_as_of": result.provenance.data_as_of,
            }
            print(json.dumps(summary[key]))
    entries = {**geo_rec.entries, **soc_rec.entries}
    save_cassette(FIXTURES / "registrations_cassette.json", entries)
    (FIXTURES / "registrations_cassette.meta.json").write_text(
        json.dumps(
            {
                "recorded_at": datetime.now(UTC).isoformat(),
                "page_size": PAGE_SIZE,
                "addresses": summary,
                "note": "kj4p-ruqc and tesw-yqqr have no personal fields; rows saved as received.",
            },
            indent=1,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"saved {len(entries)} responses")


def sanitize_contact_entries(entries: dict[str, dict]) -> dict[str, int]:
    report: dict[str, int] = {}
    for url, entry in entries.items():
        if f"/resource/{CONTACTS}.json" not in url or "count%28%2A%29" in url:
            continue
        rows = json.loads(entry["body"])
        if not isinstance(rows, list):
            continue
        original_sha = hashlib.sha256(entry["body"].encode("utf-8")).hexdigest()
        out, events = [], []
        for row in rows:
            new, withheld = sanitize_contact(row)
            out.append(new)
            if withheld:
                events.append({":id": row.get(":id"), "withheld": withheld})
        entry["body"] = json.dumps(out, ensure_ascii=False)
        entry["original_body_sha256"] = original_sha
        entry["redaction_events"] = events
        entry["redaction"] = "contact person fields and unit-level address -> [WITHHELD]"
        report[url] = len(events)
    return report


def record_contacts() -> None:
    geo_rec, soc_rec = RecordingTransport(), RecordingTransport()
    summary: dict[str, dict] = {}
    with geosearch_client(transport=geo_rec) as geo, socrata_client(transport=soc_rec) as soc:
        for key, (address, why) in ADDRESSES.items():
            res = resolve_address(address, geo, soc)
            b = retrieve_hpd_buildings(res, soc, page_size=PAGE_SIZE)
            r = retrieve_hpd_registrations(res, soc, buildings=b, page_size=PAGE_SIZE)
            c = retrieve_hpd_contacts(
                res, soc, registrations=r, page_size=PAGE_SIZE, batch_size=CONTACT_BATCH
            )
            summary[key] = {
                "address": address,
                "why": why,
                "resolution_level": getattr(res.resolution_level, "value", None),
                "buildings_status": b.status.value,
                "registrations_status": r.status.value,
                "status": c.status.value,
                "source_count": c.provenance.source_count,
                "contacts": len(c.records),
                "batches": len(c.batch_provenance),
                "data_as_of": c.provenance.data_as_of,
            }
            print(json.dumps(summary[key]))
    entries = {**geo_rec.entries, **soc_rec.entries}
    sanitized = sanitize_contact_entries(entries)
    save_cassette(FIXTURES / "contacts_cassette.json", entries)
    (FIXTURES / "contacts_cassette.meta.json").write_text(
        json.dumps(
            {
                "recorded_at": datetime.now(UTC).isoformat(),
                "page_size": PAGE_SIZE,
                "contact_batch_size": CONTACT_BATCH,
                "addresses": summary,
                "sanitized_rows_per_url": sanitized,
                "note": "Contact rows sanitized before saving (see the script docstring); "
                "originals are not in git.",
            },
            indent=1,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"saved {len(entries)} responses")


CONTACT_BATCH = 4  # small on purpose: the 8-registration lot needs two batches
STEPS = {
    "buildings": record_buildings,
    "registrations": record_registrations,
    "contacts": record_contacts,
}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in STEPS:
        sys.exit(f"usage: record_hpd_admin_fixtures.py {{{','.join(STEPS)}}}")
    STEPS[sys.argv[1]]()
