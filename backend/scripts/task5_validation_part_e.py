"""Task 5 step 6, Part E: independent 311 aggregate verification (read-only).

    cd backend && PYTHONPATH=. uv run python scripts/task5_validation_part_e.py

A separate implementation of the 311 counts: plain HTTP (httpx) and grouped SoQL, with its
own Footprints lot-family lookup. It does not call the adapter, the pager or the query
builders to get its numbers. For each acceptance address it asks, on the lot family,
created since 2020-01-01:

- counts grouped by agency and month (date_trunc_ym), all agencies, NULL included;
- the same answer re-asked until two consecutive answers carry the same data version.

It then compares, without changing anything:
1. with a live adapter run made right after (retained rows by agency and month,
   excluded HPD/DOB counts, NULL-agency count, status);
2. with the committed recordings (2026-10-23 04:51Z): totals by agency and month,
   explaining differences by time and data version, never by editing either side;
3. HPD-routed 311 keys against HPD complaints `ygpa-z7cr` (`unique_key`): matched on the
   same lot, matched elsewhere, not found.

Privacy: plain `unique_key` values exist only in memory for step 3 and are never written,
logged or printed; the outputs hold counts, agency codes, months and BBLs only. The
adapter run uses a throwaway digest key; its tokens are not written either.

Writes docs/task5/validation/part_e_311_aggregates.json and .md.
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
OUT = ROOT / "docs" / "task5" / "validation"
BASE = "https://data.cityofnewyork.us/resource/{}.json"
SR = "erm2-nwe9"
FOOTPRINTS = "5zhs-2jue"
HPD_COMPLAINTS = "ygpa-z7cr"
SINCE = "2020-01-01T00:00:00"
TRUTH = "x-soda2-truth-last-modified"
MAX_TRIES = 4
KEY_BATCH = 100


class Soql:
    def __init__(self) -> None:
        self.client = httpx.Client(timeout=300, headers={"Accept": "application/json"})
        self.log: list[dict] = []

    def get(self, dataset: str, label: str, params: dict) -> tuple[list[dict], str | None]:
        started = time.monotonic()
        resp = self.client.get(BASE.format(dataset), params=params)
        resp.raise_for_status()
        rows = resp.json()
        header = resp.headers.get(TRUTH)
        # The log never holds a key filter (key batches are logged by label only).
        logged = {k: v for k, v in params.items() if "unique_key in" not in str(v)}
        self.log.append(
            {
                "dataset": dataset,
                "label": label,
                "params": logged,
                "rows": len(rows),
                "truth_header": header,
                "seconds": round(time.monotonic() - started, 1),
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )
        return rows, header

    def stable(self, dataset: str, label: str, params: dict) -> tuple[list[dict], str | None, bool]:
        """Ask until two consecutive answers carry the same version and the same rows."""
        prev = None
        for _ in range(MAX_TRIES):
            rows, header = self.get(dataset, label, params)
            # Socrata does not promise row order for a grouped answer: compare as a set.
            canon = (header, sorted(json.dumps(r, sort_keys=True) for r in rows))
            if prev is not None and prev[0] == canon:
                return rows, header, True
            prev = (canon, rows, header)
        return prev[1], prev[2], False


def family(q: Soql, bbl: str) -> list[str]:
    rows, _ = q.get(
        FOOTPRINTS,
        "footprints lot family",
        {
            "$select": "base_bbl, mappluto_bbl",
            "$where": f"base_bbl='{bbl}' OR mappluto_bbl='{bbl}'",
            "$limit": 5000,
        },
    )
    fam = {bbl}
    for r in rows:
        for f in ("base_bbl", "mappluto_bbl"):
            v = str(r.get(f) or "").split(".")[0]
            if len(v) == 10 and v.isdigit():
                fam.add(v)
    return sorted(fam)


def lot_where(fam: list[str]) -> str:
    return "bbl in (" + ", ".join(f"'{b}'" for b in fam) + f") AND created_date >= '{SINCE}'"


def grouped(q: Soql, fam: list[str]) -> tuple[dict, str | None, bool]:
    rows, header, stable = q.stable(
        SR,
        "311 by agency and month",
        {
            "$select": "agency, date_trunc_ym(created_date) AS ym, count(*) AS n",
            "$where": lot_where(fam),
            "$group": "agency, ym",
            "$limit": 50000,
        },
    )
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        agency = r.get("agency") or "(null)"
        out.setdefault(agency, {})[r["ym"][:7]] = int(r["n"])
    return out, header, stable


def keys(q: Soql, dataset: str, label: str, where: str) -> set[str]:
    rows, _ = q.get(dataset, label, {"$select": "unique_key", "$where": where, "$limit": 50000})
    return {str(r["unique_key"]) for r in rows if r.get("unique_key")}


def hpd_cross_match(q: Soql, fam: list[str]) -> dict:
    routed = keys(q, SR, "311 HPD-routed keys (not logged)", lot_where(fam) + " AND agency='HPD'")
    rows, _ = q.get(
        HPD_COMPLAINTS,
        "HPD complaints keys and BINs on the lot family (keys not logged)",
        {
            "$select": "unique_key, bin",
            "$where": "bbl in ("
            + ", ".join(f"'{b}'" for b in fam)
            + ") AND unique_key IS NOT NULL",
            "$limit": 50000,
        },
    )
    on_lot = {str(r["unique_key"]) for r in rows}
    bins_of: dict[str, set] = {}
    for r in rows:
        bins_of.setdefault(str(r["unique_key"]), set()).add(r.get("bin") or "(none)")
    no_bbl: Counter = Counter()
    other_lot: Counter = Counter()
    rest = sorted(routed - on_lot)
    for i in range(0, len(rest), KEY_BATCH):
        batch = rest[i : i + KEY_BATCH]
        where = "unique_key in (" + ", ".join(f"'{k}'" for k in batch) + ")"
        rows, _ = q.get(
            HPD_COMPLAINTS,
            "HPD complaints key lookup (keys not logged)",
            {"$select": "unique_key, bbl", "$where": where, "$limit": 50000},
        )
        for r in rows:
            target = no_bbl if not r.get("bbl") else other_lot
            target[str(r["unique_key"])] += 1
    found_elsewhere = set(no_bbl) | set(other_lot)
    result = {
        "hpd_routed_311_rows": len(routed),
        "matched_on_lot_family": len(routed & on_lot),
        "matched_hpd_row_without_bbl": len(set(no_bbl) - set(other_lot)),
        "matched_on_another_lot": len(set(other_lot)),
        "not_found_in_hpd_complaints": len(set(rest) - found_elsewhere),
        # 311 keys matched on the lot, by the BIN(s) HPD gives them (a key with HPD rows
        # on two BINs counts once for each)
        "matched_on_lot_by_hpd_bin": dict(
            sorted(Counter(b for k in routed & on_lot for b in bins_of.get(k, ())).items())
        ),
    }
    bins_of.clear()
    for held in (routed, on_lot, no_bbl, other_lot):
        held.clear()
    return result


def by_agency_month(records) -> dict[str, dict[str, int]]:
    out: dict[str, Counter] = {}
    for r in records:
        ym = r.received_date.value.strftime("%Y-%m") if r.received_date.value else "(no date)"
        out.setdefault(r.agency or "(null)", Counter())[ym] += r.collapsed_copies
    return {a: dict(c) for a, c in out.items()}


def totals(table: dict[str, dict[str, int]]) -> dict[str, int]:
    return {a: sum(m.values()) for a, m in sorted(table.items())}


def diff(a: dict[str, dict[str, int]], b: dict[str, dict[str, int]]) -> list[dict]:
    out = []
    for agency in sorted(set(a) | set(b)):
        for ym in sorted(set(a.get(agency, {})) | set(b.get(agency, {}))):
            x, y = a.get(agency, {}).get(ym, 0), b.get(agency, {}).get(ym, 0)
            if x != y:
                out.append({"agency": agency, "month": ym, "independent": x, "other": y})
    return out


def live_adapter(address: str):
    from nyc_apartment_agent.config import Settings
    from nyc_apartment_agent.records.evidence import InMemoryEvidenceStore
    from nyc_apartment_agent.records.sr311 import retrieve_service_requests
    from nyc_apartment_agent.resolver import resolve_address
    from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

    settings = Settings(_env_file=None, evidence_digest_key=secrets.token_hex(16))
    with geosearch_client(settings) as g, socrata_client(settings) as s:
        res = resolve_address(address, g, s)
        return retrieve_service_requests(
            res, s, settings=settings, evidence=InMemoryEvidenceStore(), today=dt.date.today()
        )


def run() -> dict:
    sys.path.insert(0, str(BACKEND))
    from tests.db import task5_recorded as rec

    q = Soql()
    out = []
    for key in rec.ADDRESSES:
        recorded = rec.build(key).sr311
        bbl = recorded.resolved_bbl
        fam = family(q, bbl)
        table, header, stable = grouped(q, fam)
        live = live_adapter(rec.HPD_META["addresses"][key]["address"])
        table_after, header_after, _ = grouped(q, fam)  # bracket the adapter run
        retained = {a: m for a, m in table.items() if a not in ("HPD", "DOB", "(null)")}
        excluded = {a: sum(table.get(a, {}).values()) for a in ("HPD", "DOB")}
        null_rows = sum(table.get("(null)", {}).values())
        live_table = by_agency_month(live.records)
        rec_table = by_agency_month(recorded.records)
        ex = live.excluded_counts
        entry = {
            "address_key": key,
            "resolved_bbl": bbl,
            "independent_lot_family": fam,
            "adapter_lot_family": live.lot_family,
            "independent_truth_header": header,
            "independent_answer_stable": stable,
            "independent_header_after_adapter": header_after,
            "unchanged_across_adapter_run": table == table_after,
            "independent": {
                "retained_total": sum(totals(retained).values()),
                "retained_by_agency": totals(retained),
                "excluded": excluded,
                "null_agency_rows": null_rows,
                "by_agency_month": table,
            },
            "live_adapter": {
                "status": live.status.value,
                "accepted_data_version": live.accepted_data_version,
                "data_versions_seen": live.data_versions_seen,
                "retained_total": sum(r.collapsed_copies for r in live.records),
                "retained_by_agency": totals(live_table),
                "excluded_established": None if ex is None else ex.by_agency.established,
                "excluded_counts": None if ex is None else ex.by_agency.counts,
                "excluded_counts_observed": None if ex is None else ex.by_agency.counts_observed,
                "excluded_not_established_reason": None
                if ex is None
                else ex.by_agency.not_established_reason,
                "null_agency": None
                if ex is None
                else {
                    "established": ex.null_agency.established,
                    "observed": ex.null_agency.counts_observed,
                    "reason": ex.null_agency.not_established_reason,
                },
                "agency_month_differences": diff(retained, live_table),
            },
            "recording": {
                "status": recorded.status.value,
                "data_versions_seen": recorded.data_versions_seen,
                "accepted_data_version": recorded.accepted_data_version,
                "retained_total": sum(r.collapsed_copies for r in recorded.records),
                "retained_by_agency": totals(rec_table),
                "excluded_counts_observed": None
                if recorded.excluded_counts is None
                else recorded.excluded_counts.by_agency.counts_observed,
                "agency_month_differences": diff(retained, rec_table),
            },
            "hpd_cross_match": hpd_cross_match(q, fam),
        }
        out.append(entry)
    return {
        "generated_by": "backend/scripts/task5_validation_part_e.py",
        "window": f"created_date >= {SINCE}",
        "note": "A new live query is independent validation, not a replacement for the "
        "recording. Counts here are aggregates; no 311 key is written.",
        "addresses": out,
        "query_log": q.log,
    }


def markdown(d: dict) -> str:
    lines = [
        "# Part E: independent 311 aggregate verification",
        "",
        "Automated, read-only, separate implementation (plain HTTP and grouped SoQL; its own",
        "Footprints lot lookup). Generated by `backend/scripts/task5_validation_part_e.py`.",
        "Outputs are counts only; no 311 key is written anywhere.",
        "",
        "## Independent query vs live adapter (same time)",
        "",
        "| Address | Independent version | Stable | Retained (indep.) | Retained (adapter) | "
        "Adapter status | Agency x month differences | Lot family agrees |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for a in d["addresses"]:
        i, live = a["independent"], a["live_adapter"]
        steady = a["independent_answer_stable"] and a["unchanged_across_adapter_run"]
        lines.append(
            f"| {a['address_key']} | {a['independent_truth_header']} | "
            f"{'yes' if steady else 'no'}"
            f" | {i['retained_total']} | {live['retained_total']} | {live['status']} | "
            f"{len(live['agency_month_differences'])} | "
            f"{'yes' if a['independent_lot_family'] == sorted(a['adapter_lot_family']) else 'no'} |"
        )
    lines += [
        "",
        "## Exclusions and NULL agency",
        "",
        "| Address | HPD (indep.) | DOB (indep.) | Adapter HPD/DOB | Established by adapter | "
        "NULL agency (indep.) | Adapter NULL check |",
        "|---|---|---|---|---|---|---|",
    ]
    for a in d["addresses"]:
        i, live = a["independent"], a["live_adapter"]
        shown = live["excluded_counts"] or live["excluded_counts_observed"] or {}
        null = live["null_agency"] or {}
        lines.append(
            f"| {a['address_key']} | {i['excluded'].get('HPD', 0)} | {i['excluded'].get('DOB', 0)} "
            f"| {shown.get('HPD', '')}/{shown.get('DOB', '')} | {live['excluded_established']} | "
            f"{i['null_agency_rows']} | {(null.get('observed') or {}).get('null_agency', '')} "
            f"({'established' if null.get('established') else 'not established'}) |"
        )
    lines += [
        "",
        "## Independent query (now) vs the committed recording (2026-10-23 04:51Z)",
        "",
        "| Address | Recorded retained | Recorded status | Independent retained now | "
        "Agency x month cells that differ | Recorded HPD/DOB (observed) | "
        "Independent HPD/DOB now |",
        "|---|---|---|---|---|---|---|",
    ]
    for a in d["addresses"]:
        r, i = a["recording"], a["independent"]
        obs = r["excluded_counts_observed"] or {}
        lines.append(
            f"| {a['address_key']} | {r['retained_total']} | {r['status']} | "
            f"{i['retained_total']} | {len(r['agency_month_differences'])} | "
            f"{obs.get('HPD', '')}/{obs.get('DOB', '')} | {i['excluded'].get('HPD', 0)}/"
            f"{i['excluded'].get('DOB', 0)} |"
        )
    lines += [
        "",
        "Differences against the recording are expected: rows created or updated since the",
        "recording, and the recording's own version limits (1134 West Farms and 1845 ACP",
        "were recorded across two data versions and stay incomplete). Neither side is edited.",
        "",
        "## HPD-routed 311 rows vs HPD complaints (ygpa-z7cr, by key, counts only)",
        "",
        "| Address | HPD-routed 311 rows | Matched on the lot family | Matched, HPD row has "
        "no BBL | Matched on another lot | Not found |",
        "|---|---|---|---|---|---|",
    ]
    for a in d["addresses"]:
        h = a["hpd_cross_match"]
        lines.append(
            f"| {a['address_key']} | {h['hpd_routed_311_rows']} | {h['matched_on_lot_family']} | "
            f"{h['matched_hpd_row_without_bbl']} | {h['matched_on_another_lot']} | "
            f"{h['not_found_in_hpd_complaints']} |"
        )
    lines += [
        "",
        "The match rate is reported, not asserted. Keys matched on the lot, by the BIN that "
        "HPD's complaint rows give them:",
        "",
    ]
    for a in d["addresses"]:
        lines.append(f"- {a['address_key']}: {a['hpd_cross_match']['matched_on_lot_by_hpd_bin']}")
    lines.append("")
    for a in d["addresses"]:
        diffs = a["live_adapter"]["agency_month_differences"]
        if diffs:
            lines += [f"### {a['address_key']}: independent vs adapter cells that differ", ""]
            lines += ["| Agency | Month | Independent | Adapter |", "|---|---|---|---|"]
            lines += [
                f"| {x['agency']} | {x['month']} | {x['independent']} | {x['other']} |"
                for x in diffs
            ]
            lines.append("")
    return "\n".join(lines)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if sys.argv[1:] == ["--render"]:  # rewrite the .md from the saved .json, no queries
        d = json.loads((OUT / "part_e_311_aggregates.json").read_text(encoding="utf-8"))
        (OUT / "part_e_311_aggregates.md").write_text(markdown(d), encoding="utf-8")
        return
    d = run()
    (OUT / "part_e_311_aggregates.json").write_text(
        json.dumps(d, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (OUT / "part_e_311_aggregates.md").write_text(markdown(d), encoding="utf-8")
    for a in d["addresses"]:
        print(
            a["address_key"],
            a["independent"]["retained_total"],
            a["live_adapter"]["retained_total"],
            a["live_adapter"]["status"],
            len(a["live_adapter"]["agency_month_differences"]),
            a["hpd_cross_match"],
        )


if __name__ == "__main__":
    main()
