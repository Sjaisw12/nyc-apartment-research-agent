"""Gather independent evidence for the draft test-address set (no labels are invented).

Run from backend/:  uv run python scripts/collect_address_evidence.py

For every row in data/eval/candidate_addresses_draft.csv this records what three
separately maintained city datasets say, so a human can review and approve labels:

1. GeoSearch (built on DCP's Property Address Directory, PAD): top 5 candidates.
2. PLUTO (DCP tax-lot table, 64uk-42ks) looked up by a hand-written lot address,
   then Building Footprints (OTI, 5zhs-2jue) for the BINs physically on that lot.
3. HPD "Buildings Subject to HPD Jurisdiction" (kj4p-ruqc) by house number and street.
4. A cross-check of GeoSearch's top candidate: the PLUTO lot for its BBL, the footprint
   BINs on that lot, where Footprints places its BIN, and HPD's record for that BIN.

PLUTO/Footprints/HPD are not fully independent of PAD (they share city address
standards), but they are maintained separately, so agreement is meaningful evidence.
Output: data/eval/candidate_addresses_evidence.json
"""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nyc_apartment_agent.errors import SourceError
from nyc_apartment_agent.sources import geosearch
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

REPO = Path(__file__).resolve().parents[2]
DRAFT = REPO / "data" / "eval" / "candidate_addresses_draft.csv"
OUT = REPO / "data" / "eval" / "candidate_addresses_evidence.json"

PLUTO = "/resource/64uk-42ks.json"
FOOTPRINTS = "/resource/5zhs-2jue.json"
HPD_BUILDINGS = "/resource/kj4p-ruqc.json"


def q(value: str) -> str:
    """Quote a literal for SoQL."""
    return "'" + value.replace("'", "''") + "'"


def pluto_lookup(sc, borough: str, address: str) -> list[dict[str, Any]]:
    resp = sc.get_json(
        PLUTO,
        params={
            "$select": "bbl,borough,address,bldgclass,numbldgs,unitsres,condono,version",
            "$where": f"borough={q(borough)} AND address={q(address)}",
            "$limit": 10,
        },
    )
    rows = resp.data
    for row in rows:
        row["bbl"] = str(int(float(row["bbl"])))  # PLUTO returns "2027540005.00000000"
    return rows


def footprint_bins(sc, bbl: str) -> list[dict[str, Any]]:
    resp = sc.get_json(
        FOOTPRINTS,
        params={
            "$select": "bin,base_bbl,mappluto_bbl,construction_year,last_status_type",
            "$where": f"base_bbl={q(bbl)} OR mappluto_bbl={q(bbl)}",
            "$order": "bin",
            "$limit": 200,
        },
    )
    return resp.data


def pluto_by_bbl(sc, bbl: str) -> list[dict[str, Any]]:
    resp = sc.get_json(
        PLUTO,
        params={
            "$select": "bbl,borough,address,bldgclass,numbldgs,unitsres,condono,version",
            "$where": f"bbl={bbl}",  # numeric column; bbl is validated by GeoSearch parsing
            "$limit": 1,
        },
    )
    rows = resp.data
    for row in rows:
        row["bbl"] = str(int(float(row["bbl"])))
    return rows


def footprint_of_bin(sc, bin_value: str) -> list[dict[str, Any]]:
    resp = sc.get_json(
        FOOTPRINTS,
        params={"$select": "bin,base_bbl,mappluto_bbl,last_status_type", "bin": bin_value},
    )
    return resp.data


def hpd_by_bin(sc, bin_value: str) -> list[dict[str, Any]]:
    resp = sc.get_json(
        HPD_BUILDINGS,
        params={
            "$select": "buildingid,boroid,housenumber,streetname,block,lot,bin,recordstatus",
            "bin": bin_value,
            "$limit": 10,
        },
    )
    rows = resp.data
    for row in rows:
        row["bbl"] = f"{row['boroid']}{int(row['block']):05d}{int(row['lot']):04d}"
    return rows


def hpd_lookup(sc, boroid: str, housenumber: str, street: str) -> list[dict[str, Any]]:
    resp = sc.get_json(
        HPD_BUILDINGS,
        params={
            "$select": "buildingid,boroid,housenumber,streetname,zip,block,lot,bin,"
            "recordstatus,lifecycle,legalclassa",
            "$where": f"boroid={q(boroid)} AND housenumber={q(housenumber)} "
            f"AND streetname={q(street)}",
            "$limit": 10,
        },
    )
    rows = resp.data
    for row in rows:
        row["bbl"] = f"{row['boroid']}{int(row['block']):05d}{int(row['lot']):04d}"
    return rows


def main() -> None:
    rows = list(csv.DictReader(DRAFT.open(encoding="utf-8")))
    results = []
    with geosearch_client() as gs, socrata_client() as sc:
        for row in rows:
            entry: dict[str, Any] = {"id": row["id"], "query": row["query"], "errors": []}
            try:
                found = geosearch.search(gs, row["query"], size=5)
                entry["geosearch"] = [
                    {
                        "label": c.label,
                        "bbl": c.bbl,
                        "bin": c.bin,
                        "bin_status": c.bin_status.value,
                        "match_type": c.match_type,
                        "confidence": c.confidence,
                        "pad_version": c.pad_version,
                    }
                    for c in found.candidates
                ]
            except SourceError as exc:
                entry["errors"].append(str(exc))

            entry["pluto"] = []
            for spec in filter(None, row["pluto_lookups"].split(";")):
                borough, address = spec.split("|", 1)
                try:
                    for lot in pluto_lookup(sc, borough, address):
                        lot["footprint_bins"] = footprint_bins(sc, lot["bbl"])
                        entry["pluto"].append({"lookup": spec, **lot})
                except SourceError as exc:
                    entry["errors"].append(str(exc))

            entry["hpd_buildings"] = []
            if row["hpd_lookup"]:
                boroid, number, street = row["hpd_lookup"].split("|")
                try:
                    entry["hpd_buildings"] = hpd_lookup(sc, boroid, number, street)
                except SourceError as exc:
                    entry["errors"].append(str(exc))

            # Cross-check GeoSearch's top candidate against the other datasets.
            top = (entry.get("geosearch") or [None])[0]
            if top and top["bbl"]:
                try:
                    check: dict[str, Any] = {"bbl": top["bbl"], "bin": top["bin"]}
                    check["pluto_lot"] = pluto_by_bbl(sc, top["bbl"])
                    check["footprint_bins_on_lot"] = sorted(
                        {f["bin"] for f in footprint_bins(sc, top["bbl"])}
                    )
                    if top["bin_status"] == "assigned":
                        check["footprint_of_bin"] = footprint_of_bin(sc, top["bin"])
                        check["hpd_by_bin"] = hpd_by_bin(sc, top["bin"])
                    entry["top_candidate_cross_check"] = check
                except SourceError as exc:
                    entry["errors"].append(str(exc))

            results.append(entry)
            print(row["id"], "done", "errors:" if entry["errors"] else "", *entry["errors"])

    OUT.write_text(
        json.dumps(
            {"collected_at": datetime.now(UTC).isoformat(), "rows": results},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
