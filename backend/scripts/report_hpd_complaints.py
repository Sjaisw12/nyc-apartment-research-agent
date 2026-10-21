"""Replay the recorded HPD complaints cassette and write the Task 2 results (offline).

    uv run python scripts/report_hpd_complaints.py

Writes data/eval/hpd_task2_results.json: per address, the resolver outcome, retrieval
status, provenance and summary (problem and complaint counts kept separate). No
apartment detail, anonymity flag or 311 request number is included.
"""

from __future__ import annotations

import json
from pathlib import Path

from nyc_apartment_agent.cassette import ReplayTransport, load_cassette
from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.records.hpd_complaints import retrieve_hpd_complaints
from nyc_apartment_agent.resolver import resolve_address
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "backend" / "tests" / "fixtures" / "hpd"
OUT = ROOT / "data" / "eval" / "hpd_task2_results.json"


def main() -> None:
    meta = json.loads((FIXTURES / "complaints_cassette.meta.json").read_text(encoding="utf-8"))
    entries = load_cassette(FIXTURES / "complaints_cassette.json")
    settings = Settings(_env_file=None)
    out: dict[str, dict] = {"recorded_at": meta["recorded_at"], "addresses": {}}
    for key, info in meta["addresses"].items():
        transport = ReplayTransport(entries)
        with (
            geosearch_client(settings, transport) as g,
            socrata_client(settings, transport) as s,
        ):
            res = resolve_address(info["address"], g, s)
            result = retrieve_hpd_complaints(res, s, page_size=meta["page_size"])
        assert transport.misses == [], transport.misses
        out["addresses"][key] = {
            "address": info["address"],
            "resolver": {
                "outcome": res.outcome.value,
                "resolution_level": getattr(res.resolution_level, "value", None),
                "bbl": res.building.bbl if res.building else None,
                "bin": res.building.bin if res.building else None,
                "permitted_join_keys": res.permitted_join_keys,
            },
            "status": result.status.value,
            "status_detail": result.status_detail,
            "source_count": result.provenance.source_count,
            "problems": len(result.records),
            "complaints": len(result.complaints),
            "pages": [p.rows for p in result.provenance.pages],
            "data_as_of": result.provenance.data_as_of,
            "summary": result.summary.model_dump(mode="json") if result.summary else None,
        }
        print(key, result.status.value, len(result.records), len(result.complaints))
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
