"""Record privacy-safe DOB and 311 responses for the five test addresses (Task 5, step 1).

    uv run python scripts/record_task5_fixtures.py

Research only: there is no Task 5 adapter yet. The script runs the queries planned in
docs/phase2_task5_dob_311_design.md (section 8 and the step-1 amendments) through the
existing pager, records every HTTP response, sanitizes the rows, and saves one cassette
per dataset under tests/fixtures/dob311/. If the step-2 adapters build different URLs,
they re-record with this same sanitizer and the difference is reported.

Sanitizing, before anything is written:
- 311 (erm2-nwe9): `unique_key` -> [WITHHELD], as in every committed fixture (the
  global fixture scan requires it), plus a separate `fixture_row_ref` FX000001...,
  numbered in fetch order and not derived from the key, so a later replay test can
  restore distinct keys in memory; the address as typed, street names, cross and
  intersection streets, landmark, coordinates, location and channel -> [WITHHELD];
  every timestamp keeps its date and loses its time; unit and floor detail in
  `resolution_description` is redacted.
- ECB (6bgk-3dad): respondent name and address fields and `violation_description`
  -> [WITHHELD] (free text can name people; Amendment 2).
- DOB complaints (eabe-havv): no personal fields; unchanged.
Each sanitized entry keeps the SHA-256 of the original body and per-row events (field
names and counts only). Unsanitized bodies are never written.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nyc_apartment_agent.cassette import RecordingTransport, save_cassette
from nyc_apartment_agent.records.redaction import WITHHELD, redact_text_detailed
from nyc_apartment_agent.sources.clients import socrata_client
from nyc_apartment_agent.sources.socrata_pager import fetch_all

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "dob311"
PAGE_SIZE = 1000
SINCE_311 = "2020-01-01T00:00:00"

DOB_COMPLAINTS = "eabe-havv"
ECB = "6bgk-3dad"
SR311 = "erm2-nwe9"
FOOTPRINTS = "5zhs-2jue"
HPD_BUILDINGS = "kj4p-ruqc"

# Resolved BBL and BIN from the Phase 1 resolver (benchmark labels); None = tax lot only.
ADDRESSES = {
    "west_farms_1134": ("2027540005", "2006185"),
    "west_17_220": ("1007660055", "1081543"),
    "west_farms_1217": ("2029930031", None),
    "fifth_ave_350": ("1008350041", "1015862"),
    "acp_1845": ("1018227502", "1054974"),
}

SR311_WITHHELD = (
    "incident_address",
    "street_name",
    "cross_street_1",
    "cross_street_2",
    "intersection_street_1",
    "intersection_street_2",
    "landmark",
    "latitude",
    "longitude",
    "x_coordinate_state_plane",
    "y_coordinate_state_plane",
    "location",
    "open_data_channel_type",
)
SR311_DATES = ("created_date", "closed_date", "due_date", "resolution_action_updated_date")
ECB_WITHHELD = (
    "respondent_name",
    "respondent_house_number",
    "respondent_street",
    "respondent_city",
    "respondent_zip",
    "violation_description",
)


def _lot_clause(bbl: str) -> str:
    lot4 = bbl[6:]
    return f"(boro='{bbl[0]}' AND block='{bbl[1:6]}' AND lot in ('{lot4}', '0{lot4}'))"


def _quote(values: list[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def plan(soc, key: str, bbl: str, bin_: str | None) -> dict[str, Any]:
    """BIN set and candidate lots from Footprints and HPD (design 6.3 and 6.5)."""
    fp = fetch_all(
        soc,
        FOOTPRINTS,
        where=f"base_bbl='{bbl}' OR mappluto_bbl='{bbl}'",
        order_by="bin, :id",
        unique_key=":id",
        select="bin, base_bbl, mappluto_bbl, :id",
        page_size=PAGE_SIZE,
        row_cap=5000,
    )
    hpd = fetch_all(
        soc,
        HPD_BUILDINGS,
        where=f"boroid='{bbl[0]}' AND block='{int(bbl[1:6])}' AND lot='{int(bbl[6:])}'",
        order_by="buildingid",
        unique_key="buildingid",
        select="buildingid, bin",
        page_size=PAGE_SIZE,
        row_cap=5000,
    )
    fp_bins = sorted({r["bin"] for r in fp.rows if r.get("bin")})
    hpd_bins = sorted({r["bin"] for r in hpd.rows if r.get("bin")})
    lots = sorted({bbl} | {r[f] for r in fp.rows for f in ("base_bbl", "mappluto_bbl") if r.get(f)})
    return {
        "footprints_bins": fp_bins,
        "hpd_bins": hpd_bins,
        "search_bins": sorted(set(fp_bins) | set(hpd_bins) | ({bin_} if bin_ else set())),
        "candidate_lots": lots,
    }


def _summary(fetch) -> dict[str, Any]:
    p = fetch.provenance
    return {
        "where": p.where,
        "source_count": p.source_count,
        "rows_fetched": p.rows_fetched,
        "complete": fetch.complete,
        "incomplete_reasons": p.incomplete_reasons,
        "data_as_of": p.data_as_of,
        "truth_headers": [pg.truth_last_modified for pg in p.pages],
    }


def record(soc, key: str, bbl: str, bin_: str | None) -> dict[str, Any]:
    p = plan(soc, key, bbl, bin_)
    out: dict[str, Any] = {
        "bbl": bbl,
        "resolved_bin": bin_,
        "join_scope": "building" if bin_ else "tax_lot",
        **p,
    }
    if bin_:
        dob = fetch_all(
            soc,
            DOB_COMPLAINTS,
            where=f"bin in ({_quote(p['search_bins'])})",
            order_by=":id",
            unique_key=":id",
            select="*, :id",
            page_size=PAGE_SIZE,
            row_cap=10_000,
        )
        out["dob_complaints"] = _summary(dob)
    else:
        out["dob_complaints"] = {"status": "unsupported_join", "reason": "tax-lot scope"}
    lot_where = " OR ".join(_lot_clause(c) for c in p["candidate_lots"])
    ecb_where = f"{lot_where} OR bin='{bin_}'" if bin_ else lot_where
    ecb = fetch_all(
        soc,
        ECB,
        where=ecb_where,
        order_by="isn_dob_bis_extract",
        unique_key="isn_dob_bis_extract",
        page_size=PAGE_SIZE,
        row_cap=10_000,
    )
    out["ecb"] = _summary(ecb)
    lots = _quote(p["candidate_lots"])
    sr = fetch_all(
        soc,
        SR311,
        where=f"bbl in ({lots}) AND created_date >= '{SINCE_311}' AND agency not in ('HPD', 'DOB')",
        order_by="unique_key",
        unique_key="unique_key",
        page_size=PAGE_SIZE,
        row_cap=10_000,
    )
    out["sr311"] = _summary(sr)
    excl = soc.get_json(
        f"/resource/{SR311}.json",
        params={
            "$select": "agency, count(*) AS n",
            "$where": f"bbl in ({lots}) AND created_date >= '{SINCE_311}' "
            "AND agency in ('HPD', 'DOB')",
            "$group": "agency",
        },
    )
    out["sr311_excluded_source_counts"] = {
        "counts": {r["agency"]: int(r["n"]) for r in excl.data},
        "truth_header": excl.headers.get("x-soda2-truth-last-modified"),
        "note": "a separate count query; not retrieved rows",
    }
    return out


def _is_rows(url: str, dataset: str) -> bool:
    return f"/resource/{dataset}.json" in url and "count%28%2A%29" not in url


def sanitize(entries: dict[str, dict]) -> dict[str, Any]:
    report: dict[str, Any] = {}
    seq = 0
    for url in sorted(entries):  # deterministic pseudonym order
        entry = entries[url]
        body = entry["body"]
        rows = json.loads(body)
        if not isinstance(rows, list):
            continue
        events: list[dict[str, Any]] = []
        if _is_rows(url, SR311) and rows and "unique_key" in rows[0]:
            for i, row in enumerate(rows):
                ev: dict[str, Any] = {"row": i, "withheld": [], "dates_truncated": []}
                if row.get("unique_key"):
                    seq += 1
                    row["unique_key"] = WITHHELD
                    row["fixture_row_ref"] = f"FX{seq:06d}"
                for f in SR311_WITHHELD:
                    if row.get(f) not in (None, "", WITHHELD):
                        row[f] = WITHHELD
                        ev["withheld"].append(f)
                for f in SR311_DATES:
                    v = row.get(f)
                    if isinstance(v, str) and "T" in v and not v.endswith("T00:00:00.000"):
                        row[f] = v.split("T")[0] + "T00:00:00.000"
                        ev["dates_truncated"].append(f)
                text, counts = redact_text_detailed(row.get("resolution_description"))
                if counts:
                    row["resolution_description"] = text
                    ev["resolution_description_redactions"] = counts
                events.append(ev)
        elif _is_rows(url, ECB) and rows and "isn_dob_bis_extract" in rows[0]:
            for i, row in enumerate(rows):
                done = [f for f in ECB_WITHHELD if row.get(f) not in (None, "", WITHHELD)]
                for f in done:
                    row[f] = WITHHELD
                events.append({"row": i, "withheld": done})
        else:
            continue
        entry["original_body_sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
        entry["body"] = json.dumps(rows, ensure_ascii=False)
        entry["redaction_events"] = events
        report[url] = len(rows)
    return report


def main() -> None:
    rec = RecordingTransport()
    summary: dict[str, Any] = {}
    with socrata_client(transport=rec) as soc:
        for key, (bbl, bin_) in ADDRESSES.items():
            summary[key] = record(soc, key, bbl, bin_)
            print(key, json.dumps({k: summary[key][k] for k in ("search_bins", "candidate_lots")}))
    sanitized = sanitize(rec.entries)
    by_ds: dict[str, dict[str, dict]] = {}
    for url, entry in rec.entries.items():
        ds = url.split("/resource/")[1].split(".json")[0]
        by_ds.setdefault(ds, {})[url] = entry
    names = {
        DOB_COMPLAINTS: "dob_complaints",
        ECB: "dob_ecb",
        SR311: "sr311",
        FOOTPRINTS: "footprints",
        HPD_BUILDINGS: "hpd_buildings",
    }
    for ds, entries in by_ds.items():
        save_cassette(FIXTURES / f"{names[ds]}_cassette.json", entries)
    (FIXTURES / "cassettes.meta.json").write_text(
        json.dumps(
            {
                "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "page_size": PAGE_SIZE,
                "sanitizing": __doc__.split("Sanitizing, before anything is written:")[1]
                .split("Each sanitized")[0]
                .strip(),
                "addresses": summary,
                "sanitized_rows_per_url": sanitized,
            },
            indent=1,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"saved {len(rec.entries)} responses to {FIXTURES}")


if __name__ == "__main__":
    main()
