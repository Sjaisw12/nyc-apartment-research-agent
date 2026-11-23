"""Task 5, step 1: measure DOB and 311 source facts (research only, no adapter).

    uv run python scripts/task5_measure.py [--out PATH]

Runs read-only SoQL queries against data.cityofnewyork.us and writes one JSON file of
aggregates. Privacy rules for the output:
- counts, value vocabularies of coded fields, and pattern-match counts only;
- free-text fields are scanned locally and only counts are written, never the text;
- a field's values are listed only if each listed value occurs at least LIST_MIN times
  (rare values of a possibly free-text field are counted, not printed);
- no 311 unique_key, Socrata :id, respondent name, address as typed, or coordinates.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.records.redaction import redact_text_detailed
from nyc_apartment_agent.sources.clients import socrata_client

DOB_COMPLAINTS = "eabe-havv"
ECB = "6bgk-3dad"
SR311 = "erm2-nwe9"
DISPOSITION_CODES = "6v9u-ndjg"
FOOTPRINTS = "5zhs-2jue"
TRUTH = "x-soda2-truth-last-modified"
LIST_MIN = 20
OUT = Path(__file__).resolve().parents[2] / "docs" / "task5" / "step1_measurements.json"

# The five Phase 1 / Task 1-3 test addresses: resolved BBL and BIN (None = tax lot only).
ADDRESSES = {
    "west_farms_1134": ("2027540005", "2006185"),
    "west_17_220": ("1007660055", "1081543"),
    "west_farms_1217": ("2029930031", None),
    "fifth_ave_350": ("1008350041", "1015862"),
    "acp_1845": ("1018227502", "1054974"),
}

PHONE = re.compile(r"\b\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
HONORIFIC = re.compile(r"\b(?:MR|MRS|MS|MISS|DR)\.?\s+[A-Z]{2,}", re.IGNORECASE)
APT_WORD = re.compile(r"\b(?:APT|APTS|APARTMENT|UNIT)\b", re.IGNORECASE)
HASH_NUM = re.compile(r"#\s*\d")
NOV_HASH = re.compile(r"\bNOV\s*#", re.IGNORECASE)


class Q:
    def __init__(self, client) -> None:
        self.client = client
        self.log: list[dict[str, Any]] = []

    def __call__(self, ds: str, label: str, **params: Any) -> list[dict[str, Any]]:
        p = {f"${k}": v for k, v in params.items()}
        t0 = time.monotonic()
        resp = self.client.get_json(f"/resource/{ds}.json", params=p)
        self.log.append(
            {
                "label": label,
                "dataset": ds,
                "params": p,
                "truth_last_modified": resp.headers.get(TRUTH),
                "rows": len(resp.data),
                "seconds": round(time.monotonic() - t0, 2),
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )
        return resp.data

    def raw(self, ds: str, **params: Any):
        p = {f"${k}": v for k, v in params.items()}
        return self.client.get_json(f"/resource/{ds}.json", params=p)


def groups(rows: list[dict], key: str, n: str = "n") -> dict[str, int]:
    return {str(r.get(key)): int(r[n]) for r in rows}


def text_scan(texts: list[str | None]) -> dict[str, Any]:
    """Pattern counts over free text; the text itself is never returned."""
    c: Counter[str] = Counter()
    for t in texts:
        if not t:
            c["empty"] += 1
            continue
        c["non_empty"] += 1
        _, counts = redact_text_detailed(t)
        for k, v in counts.items():
            c[f"redactor_{k}_rows"] += 1
            c[f"redactor_{k}_replacements"] += v
        c["apt_word_rows"] += bool(APT_WORD.search(t))
        c["hash_number_rows"] += bool(HASH_NUM.search(t))
        c["nov_hash_rows"] += bool(NOV_HASH.search(t))
        c["phone_pattern_rows"] += bool(PHONE.search(t))
        c["email_pattern_rows"] += bool(EMAIL.search(t))
        c["honorific_name_rows"] += bool(HONORIFIC.search(t))
    return {"rows": len(texts), **dict(sorted(c.items()))}


def vocabulary(rows: list[dict], key: str) -> dict[str, Any]:
    """Cardinality of a field; values listed only if frequent (see LIST_MIN)."""
    counts = {str(r.get(key)): int(r["n"]) for r in rows}
    frequent = {k: v for k, v in counts.items() if v >= LIST_MIN}
    return {
        "distinct_values": len(counts),
        "rows": sum(counts.values()),
        "values_listed_min_count": LIST_MIN,
        "listed": dict(sorted(frequent.items(), key=lambda kv: -kv[1])),
        "unlisted_values": len(counts) - len(frequent),
        "unlisted_rows": sum(v for k, v in counts.items() if k not in frequent),
    }


def measure(q: Q) -> dict[str, Any]:
    out: dict[str, Any] = {}

    # 1. Vocabularies
    out["ecb_hearing_status"] = groups(
        q(
            ECB,
            "ecb hearing_status",
            select="hearing_status, count(*) AS n",
            group="hearing_status",
            limit=1000,
        ),
        "hearing_status",
    )
    out["ecb_certification_status"] = groups(
        q(
            ECB,
            "ecb certification_status",
            select="certification_status, count(*) AS n",
            group="certification_status",
            limit=1000,
        ),
        "certification_status",
    )
    out["ecb_severity"] = groups(
        q(ECB, "ecb severity", select="severity, count(*) AS n", group="severity", limit=1000),
        "severity",
    )
    out["ecb_status"] = groups(
        q(
            ECB,
            "ecb status",
            select="ecb_violation_status, count(*) AS n",
            group="ecb_violation_status",
            limit=100,
        ),
        "ecb_violation_status",
    )
    xt = q(
        ECB,
        "ecb status x hearing",
        select="ecb_violation_status, hearing_status, count(*) AS n",
        group="ecb_violation_status, hearing_status",
        limit=5000,
    )
    out["ecb_status_x_hearing"] = [
        {
            "ecb_violation_status": r.get("ecb_violation_status"),
            "hearing_status": r.get("hearing_status"),
            "n": int(r["n"]),
        }
        for r in xt
    ]
    out["ecb_violation_type"] = groups(
        q(
            ECB,
            "ecb violation_type",
            select="violation_type, count(*) AS n",
            group="violation_type",
            limit=1000,
        ),
        "violation_type",
    )

    codes = q(DISPOSITION_CODES, "disposition code table", limit=1000)
    table = {r.get("code"): r.get("disposition") for r in codes}
    out["disposition_code_table"] = dict(sorted(table.items()))
    in_use = groups(
        q(
            DOB_COMPLAINTS,
            "dob disposition_code",
            select="disposition_code, count(*) AS n",
            group="disposition_code",
            limit=5000,
        ),
        "disposition_code",
    )
    out["dob_disposition_codes_in_use"] = dict(sorted(in_use.items(), key=lambda kv: -kv[1]))
    out["dob_disposition_codes_not_in_table"] = {
        k: v for k, v in in_use.items() if k not in table and k != "None"
    }
    xs = q(
        DOB_COMPLAINTS,
        "dob status x disposition",
        select="status, disposition_code, count(*) AS n",
        group="status, disposition_code",
        limit=10000,
    )
    out["dob_status_x_disposition"] = [
        {"status": r.get("status"), "disposition_code": r.get("disposition_code"), "n": int(r["n"])}
        for r in xs
    ]
    out["dob_status"] = groups(
        q(DOB_COMPLAINTS, "dob status", select="status, count(*) AS n", group="status", limit=100),
        "status",
    )
    out["dob_complaint_category"] = groups(
        q(
            DOB_COMPLAINTS,
            "dob category",
            select="complaint_category, count(*) AS n",
            group="complaint_category",
            limit=1000,
        ),
        "complaint_category",
    )

    since = "2026-09-01T00:00:00"
    out["sr311_window"] = f"created_date >= {since}"
    out["sr311_status"] = groups(
        q(
            SR311,
            "311 status",
            select="status, count(*) AS n",
            where=f"created_date >= '{since}'",
            group="status",
            limit=1000,
        ),
        "status",
    )
    sc = q(
        SR311,
        "311 status x closed",
        select="status, closed_date IS NULL AS no_close, count(*) AS n",
        where=f"created_date >= '{since}'",
        group="status, no_close",
        limit=1000,
    )
    out["sr311_status_x_closed_date_missing"] = [
        {"status": r.get("status"), "closed_date_missing": r.get("no_close"), "n": int(r["n"])}
        for r in sc
    ]
    out["sr311_agency"] = groups(
        q(
            SR311,
            "311 agency",
            select="agency, count(*) AS n",
            where=f"created_date >= '{since}'",
            group="agency",
            limit=1000,
        ),
        "agency",
    )

    # 2. ECB identifier formats
    for field in ("block", "lot", "boro"):
        out[f"ecb_{field}_length"] = groups(
            q(
                ECB,
                f"ecb {field} length",
                select=f"length({field}) AS len, count(*) AS n",
                group="len",
                limit=100,
            ),
            "len",
        )
    out["ecb_block_non_digit"] = int(
        q(
            ECB,
            "ecb block non-digit",
            select="count(*) AS n",
            where="block IS NOT NULL AND block not like '%0%' AND block not like '%1%' "
            "AND block not like '%2%' AND block not like '%3%' AND block not like '%4%' "
            "AND block not like '%5%' AND block not like '%6%' AND block not like '%7%' "
            "AND block not like '%8%' AND block not like '%9%'",
        )[0]["n"]
    )
    out["ecb_boro_values"] = groups(
        q(ECB, "ecb boro", select="boro, count(*) AS n", group="boro", limit=100), "boro"
    )

    # 3. DOB complaint number duplicates
    dups = q(
        DOB_COMPLAINTS,
        "dob complaint_number dups",
        select="complaint_number, count(*) AS n",
        group="complaint_number",
        having="count(*) > 1",
        limit=50000,
    )
    out["dob_complaint_number_duplicates"] = {
        "numbers_with_duplicates": len(dups),
        "rows_in_duplicated_numbers": sum(int(r["n"]) for r in dups),
        "max_copies": max((int(r["n"]) for r in dups), default=0),
    }
    sample = [r["complaint_number"] for r in dups[:25]]
    if sample:
        inlist = ",".join(f"'{s}'" for s in sample)
        rows = q(
            DOB_COMPLAINTS,
            "dob dup sample rows",
            where=f"complaint_number in ({inlist})",
            limit=1000,
        )
        by: dict[str, list[dict]] = {}
        for r in rows:
            by.setdefault(r["complaint_number"], []).append(r)
        diff_fields: Counter[str] = Counter()
        identical = 0
        for rs in by.values():
            keys = set().union(*rs)
            differing = [k for k in keys if len({json.dumps(x.get(k)) for x in rs}) > 1]
            if not differing:
                identical += 1
            diff_fields.update(differing)
        out["dob_duplicate_sample"] = {
            "numbers_sampled": len(by),
            "identical_copies": identical,
            "fields_that_differ": dict(diff_fields.most_common()),
        }

    # 6 (free text). Samples scanned locally; only counts written.
    ecb_text = q(
        ECB,
        "ecb description sample",
        select="violation_description",
        where="issue_date >= '20240101'",
        order="isn_dob_bis_extract DESC",
        limit=5000,
    )
    out["ecb_description_scan_recent_5000"] = text_scan(
        [r.get("violation_description") for r in ecb_text]
    )
    for field in (
        "descriptor_2",
        "descriptor",
        "complaint_type",
        "location_type",
        "resolution_description",
        "facility_type",
        "landmark",
        "address_type",
    ):
        rows = q(
            SR311,
            f"311 {field} vocabulary",
            select=f"{field}, count(*) AS n",
            where=f"created_date >= '{since}' AND agency NOT IN ('HPD', 'DOB')",
            group=field,
            limit=50000,
        )
        out[f"sr311_{field}_vocabulary"] = vocabulary(rows, field)
    sr_text = q(
        SR311,
        "311 text sample",
        select="descriptor, descriptor_2, resolution_description",
        where=f"created_date >= '{since}' AND agency NOT IN ('HPD', 'DOB')",
        order="unique_key DESC",
        limit=5000,
    )
    for field in ("descriptor", "descriptor_2", "resolution_description"):
        out[f"sr311_{field}_scan_recent_5000"] = text_scan([r.get(field) for r in sr_text])
    out["sr311_agency_null"] = int(
        q(
            SR311,
            "311 agency null",
            select="count(*) AS n",
            where=f"created_date >= '{since}' AND agency IS NULL",
        )[0]["n"]
    )

    # Per address: counts with the planned queries, condo lots, BIN sets
    per: dict[str, Any] = {}
    for key, (bbl, bin_) in ADDRESSES.items():
        boro, block, lot = bbl[0], bbl[1:6], bbl[6:]
        a: dict[str, Any] = {"bbl": bbl, "resolved_bin": bin_}
        fp = q(
            FOOTPRINTS,
            f"{key} footprints",
            select="bin, base_bbl, mappluto_bbl",
            where=f"base_bbl='{bbl}' OR mappluto_bbl='{bbl}'",
            limit=1000,
        )
        a["footprints"] = sorted(
            [
                {
                    "bin": r.get("bin"),
                    "base_bbl": r.get("base_bbl"),
                    "mappluto_bbl": r.get("mappluto_bbl"),
                }
                for r in fp
            ],
            key=lambda r: r["bin"] or "",
        )
        hpd = q(
            "kj4p-ruqc",
            f"{key} hpd buildings",
            select="bin, buildingid",
            where=f"boroid='{boro}' AND block='{int(block)}' AND lot='{int(lot)}'",
            limit=1000,
        )
        a["hpd_bins"] = sorted({r.get("bin") for r in hpd if r.get("bin")})
        lot_bins = sorted({r["bin"] for r in fp if r.get("bin")} | set(a["hpd_bins"]))
        inl = ",".join(f"'{b}'" for b in lot_bins)
        # Footprints rows for the HPD BINs: which lot does Footprints assign them?
        if lot_bins:
            fpb = q(
                FOOTPRINTS,
                f"{key} footprints by bin",
                select="bin, base_bbl, mappluto_bbl",
                where=f"bin in ({inl})",
                limit=1000,
            )
            a["footprints_for_lot_bins"] = sorted(
                [
                    {
                        "bin": r.get("bin"),
                        "base_bbl": r.get("base_bbl"),
                        "mappluto_bbl": r.get("mappluto_bbl"),
                    }
                    for r in fpb
                ],
                key=lambda r: r["bin"] or "",
            )
            a["dob_complaints_by_bin"] = groups(
                q(
                    DOB_COMPLAINTS,
                    f"{key} dob by bin",
                    select="bin, count(*) AS n",
                    where=f"bin in ({inl})",
                    group="bin",
                    limit=1000,
                ),
                "bin",
            )
        # Candidate lots: the resolved lot plus every lot Footprints gives the lot BINs
        # (condo base lots such as 1018220001 for the 1845 ACP billing lot 1018227502).
        cand = sorted(
            {bbl}
            | {
                r.get(f)
                for r in a.get("footprints_for_lot_bins", [])
                for f in ("base_bbl", "mappluto_bbl")
                if r.get(f)
            }
        )
        a["candidate_lots"] = cand
        lot_clauses = " OR ".join(
            f"(boro='{c[0]}' AND block='{c[1:6]}' AND lot in ('{c[6:]}', '0{c[6:]}'))" for c in cand
        )
        a["ecb_by_candidate_lot"] = [
            {"block": r.get("block"), "lot": r.get("lot"), "bin": r.get("bin"), "n": int(r["n"])}
            for r in q(
                ECB,
                f"{key} ecb by lot (both formats)",
                select="block, lot, bin, count(*) AS n",
                where=lot_clauses,
                group="block, lot, bin",
                limit=1000,
            )
        ]
        if lot_bins:
            a["ecb_by_lot_bins"] = [
                {
                    "block": r.get("block"),
                    "lot": r.get("lot"),
                    "bin": r.get("bin"),
                    "n": int(r["n"]),
                }
                for r in q(
                    ECB,
                    f"{key} ecb by lot bins",
                    select="block, lot, bin, count(*) AS n",
                    where=f"bin in ({inl})",
                    group="block, lot, bin",
                    limit=1000,
                )
            ]
        a["sr311_by_candidate_lot"] = [
            {"bbl": r.get("bbl"), "agency": r.get("agency"), "n": int(r["n"])}
            for r in q(
                SR311,
                f"{key} 311 by candidate lot",
                select="bbl, agency, count(*) AS n",
                where="bbl in (" + ",".join(f"'{c}'" for c in cand) + ")",
                group="bbl, agency",
                limit=1000,
            )
        ]
        a["sr311_by_agency"] = groups(
            q(
                SR311,
                f"{key} 311 by agency",
                select="agency, count(*) AS n",
                where=f"bbl='{bbl}'",
                group="agency",
                limit=1000,
            ),
            "agency",
        )
        a["sr311_by_status"] = groups(
            q(
                SR311,
                f"{key} 311 by status",
                select="status, count(*) AS n",
                where=f"bbl='{bbl}' AND agency NOT IN ('HPD', 'DOB')",
                group="status",
                limit=1000,
            ),
            "status",
        )
        a["sr311_by_location_type"] = groups(
            q(
                SR311,
                f"{key} 311 by location_type",
                select="location_type, count(*) AS n",
                where=f"bbl='{bbl}' AND agency NOT IN ('HPD', 'DOB')",
                group="location_type",
                limit=1000,
            ),
            "location_type",
        )
        # Condo blocks: other lots on the same block with 311 rows / ECB rows
        a["sr311_block_lots"] = groups(
            q(
                SR311,
                f"{key} 311 block lots",
                select="bbl, count(*) AS n",
                where=f"bbl like '{bbl[:6]}%'",
                group="bbl",
                limit=1000,
            ),
            "bbl",
        )
        a["ecb_block_lots"] = groups(
            q(
                ECB,
                f"{key} ecb block lots",
                select="lot, count(*) AS n",
                where=f"boro='{boro}' AND (block='{block}' OR block='{int(block)}')",
                group="lot",
                limit=1000,
            ),
            "lot",
        )
        per[key] = a
    out["per_address"] = per

    # 5. Truth header stability and count discrepancies
    stab: dict[str, Any] = {}
    for ds in (DOB_COMPLAINTS, ECB, SR311):
        seen = []
        for _ in range(10):
            resp = q.raw(ds, select="count(*) AS n")
            seen.append({"truth": resp.headers.get(TRUTH), "count": int(resp.data[0]["n"])})
        stab[ds] = {
            "calls": len(seen),
            "distinct_truth_headers": sorted({s["truth"] or "missing" for s in seen}),
            "distinct_counts": sorted({s["count"] for s in seen}),
            "sequence": seen,
        }
    pairs = []
    bbl = ADDRESSES["fifth_ave_350"][0]
    where = f"bbl='{bbl}' AND created_date >= '2020-01-01T00:00:00'"
    for _ in range(10):
        c = q.raw(SR311, select="count(*) AS n", where=where)
        d = q.raw(SR311, select="created_date", where=where, order="unique_key", limit=1000)
        pairs.append(
            {
                "count_truth": c.headers.get(TRUTH),
                "data_truth": d.headers.get(TRUTH),
                "count": int(c.data[0]["n"]),
                "rows": len(d.data),
            }
        )
    stab["sr311_count_then_data_350_5th"] = {
        "where": where,
        "pairs": pairs,
        "pairs_with_different_headers": sum(p["count_truth"] != p["data_truth"] for p in pairs),
        "pairs_with_count_mismatch": sum(p["count"] != p["rows"] for p in pairs),
    }
    out["truth_header_stability"] = stab
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    settings = Settings(http_read_timeout_seconds=180.0)
    started = datetime.now(UTC).isoformat(timespec="seconds")
    with socrata_client(settings) as client:
        q = Q(client)
        result = measure(q)
    payload = {
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": "https://data.cityofnewyork.us (SODA 2 resource endpoints)",
        "privacy": __doc__.split("Privacy rules for the output:")[1].strip(),
        "results": result,
        "query_log": q.log,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
