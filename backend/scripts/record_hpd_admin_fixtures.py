"""Record live resolver + HPD administrative-record responses (Phase 2, Task 3).

    uv run python scripts/record_hpd_admin_fixtures.py buildings

One cassette per step, so each step's fixture stays fixed once reviewed:
- buildings -> tests/fixtures/hpd/buildings_cassette.json (kj4p-ruqc + catalog entry)

Same five addresses as Tasks 1 and 2. The buildings dataset holds no personal fields,
so its rows are saved as received; the fixture privacy scan still runs over them.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from record_hpd_fixtures import ADDRESSES

from nyc_apartment_agent.cassette import RecordingTransport, save_cassette
from nyc_apartment_agent.records.hpd_buildings import retrieve_hpd_buildings
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


STEPS = {"buildings": record_buildings}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in STEPS:
        sys.exit(f"usage: record_hpd_admin_fixtures.py {{{','.join(STEPS)}}}")
    STEPS[sys.argv[1]]()
