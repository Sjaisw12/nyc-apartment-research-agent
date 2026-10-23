"""Task 5, step 1: build the PROPOSED DOB disposition and ECB adjudication mappings.

    uv run python scripts/task5_mappings.py

Reads docs/task5/step1_measurements.json (from task5_measure.py) and writes
docs/task5/proposed_mappings.json and docs/task5/proposed_mappings.md. Proposed only:
Sanyam reviews them at the step-1 gate before any adapter uses them.

Every code keeps DOB's own definition text verbatim (from 6v9u-ndjg). Codes in use but
absent from that table get outcome `unknown_code`: their official definitions could
not be retrieved (the DOB PDF on nyc.gov is not reachable from this environment).
"""

from __future__ import annotations

import json
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs" / "task5"

# Outcome groups. Codes not listed fall to `other_recorded`.
OUTCOMES: dict[str, tuple[str, ...]] = {
    "violation_served": (
        "A1",
        "A2",
        "A4",
        "A5",
        "A6",
        "A8",
        "A9",
        "H3",
        "H4",
        "V3",
        "W1",
        "RK",
        "R5",
        "MB",
        "MC",
        "MD",
        "MG",
        "MJ",
        "MK",
        "ME",
        "MF",
        "MH",
        "MI",
    ),
    "violation_prepared_not_served": ("B1", "B2"),
    "order_event": (
        "A3",
        "L1",
        "L2",
        "L3",
        "K4",
        "K6",
        "Y1",
        "Y2",
        "Y3",
        "Y4",
        "P3",
        "P4",
        "RZ",
        "H5",
    ),
    "no_violation": ("I2", "MA", "R1", "R6", "WA", "WF"),
    "unsubstantiated": ("I1",),
    "no_access": ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "WB"),
    "referred": (
        "F1",
        "F2",
        "F3",
        "F4",
        "F5",
        "F6",
        "F7",
        "F8",
        "F9",
        "G1",
        "G2",
        "G3",
        "G4",
        "G5",
        "G6",
        "G7",
        "G8",
        "G9",
        "WI",
    ),
    "assigned_or_pending": (
        "D1",
        "D2",
        "D3",
        "D4",
        "D5",
        "D6",
        "D7",
        "D8",
        "D9",
        "E1",
        "E2",
        "E3",
        "E4",
        "E5",
        "E6",
        "E7",
        "E8",
        "E9",
        "EA",
        "EB",
        "EC",
        "ED",
        "EE",
        "EZ",
        "J1",
        "J3",
        "J4",
        "P2",
        "K2",
        "A7",
    ),
}
# Order events (design 5.2). A code can be both an order event and a violation.
ORDER_EVENTS = {
    "A3": "stop_work_full_issued",
    "L1": "stop_work_partial_issued",
    "K6": "stop_work_partial_issued",
    "K4": "stop_work_issued_crane_no_address",
    "L2": "stop_work_rescinded_full",
    "L3": "stop_work_rescinded_partial",
    "H3": "stop_work_noncompliance",
    "H4": "stop_work_noncompliance",
    "V3": "stop_work_noncompliance",
    "H5": "stop_all_work_no_tcao",
    "Y1": "vacate_full_issued",
    "ME": "vacate_full_issued",
    "MH": "vacate_full_issued",
    "Y3": "vacate_partial_issued",
    "MF": "vacate_partial_issued",
    "MI": "vacate_partial_issued",
    "Y2": "vacate_rescinded_full",
    "Y4": "vacate_rescinded_partial",
    "RZ": "vacate_previously_issued",
    "W1": "vacate_noncompliance",
    "P3": "padlock_issued",
    "P4": "padlock_rescinded",
}
# A complaint whose disposition is one of these outcomes is dispositioned; status ACTIVE
# with such a code is flagged status_fields_disagree (design 4.3).
FINAL = {"violation_served", "no_violation", "unsubstantiated", "referred"}

# ECB hearing_status -> adjudication. The ECB dictionary defines hearing_status only as
# "Status of the hearing", and the OATH dictionary (jz4z-kudi) gives no value list.
# Mapping rule: a value ending in "IN-VIO" or equal to "IN VIOLATION" -> upheld; DEFAULT
# -> upheld (default judgement; meaning NOT verified); the rest as below.
ADJUDICATION = {
    "IN VIOLATION": "upheld",
    "ADMIT/IN-VIO": "upheld",
    "CURED/IN-VIO": "upheld",
    "POP/IN-VIO": "upheld",
    "STIPULATION/IN-VIO": "upheld",
    "DEFAULT": "upheld_by_default",
    "DISMISSED": "dismissed",
    "PENDING": "pending",
    "WRITTEN OFF": "other",
}


def outcome(code: str | None, table: dict[str, str]) -> str:
    if code in (None, "None", ""):
        return "none"
    if code not in table:
        return "unknown_code"
    for name, codes in OUTCOMES.items():
        if code in codes:
            return name
    return "other_recorded"


def main() -> None:
    m = json.loads((DOCS / "step1_measurements.json").read_text(encoding="utf-8"))["results"]
    table: dict[str, str] = m["disposition_code_table"]
    in_use: dict[str, int] = m["dob_disposition_codes_in_use"]
    by_status: dict[str, dict[str, int]] = {}
    for r in m["dob_status_x_disposition"]:
        by_status.setdefault(str(r["disposition_code"]), {})[str(r["status"])] = r["n"]

    codes = sorted(set(table) | {c for c in in_use if c != "None"})
    rows = []
    for c in codes:
        o = outcome(c, table)
        rows.append(
            {
                "code": c,
                "dob_definition": table.get(c),
                "in_reference_table": c in table,
                "outcome": o,
                "order_event": ORDER_EVENTS.get(c),
                "violation_served": o == "violation_served",
                "rows_in_use": in_use.get(c, 0),
                "rows_by_status": by_status.get(c, {}),
            }
        )
    disagree_active = sum(
        r["rows_by_status"].get("ACTIVE", 0) for r in rows if r["outcome"] in FINAL
    )
    closed_none = by_status.get("None", {}).get("CLOSED", 0)
    active_none = by_status.get("None", {}).get("ACTIVE", 0)
    total = sum(m["dob_status"].values())
    summary = {
        "complaints_total": total,
        "active_with_final_disposition": disagree_active,
        "closed_with_no_disposition": closed_none,
        "active_with_no_disposition": active_none,
        "codes_in_use": len([c for c in in_use if c != "None"]),
        "codes_in_use_not_in_reference_table": len(m["dob_disposition_codes_not_in_table"]),
        "rows_with_codes_not_in_reference_table": sum(
            m["dob_disposition_codes_not_in_table"].values()
        ),
        "rows_by_outcome": {},
    }
    for r in rows:
        summary["rows_by_outcome"][r["outcome"]] = (
            summary["rows_by_outcome"].get(r["outcome"], 0) + r["rows_in_use"]
        )
    summary["rows_by_outcome"]["none"] = sum(by_status.get("None", {}).values())

    hearing = m["ecb_hearing_status"]
    adj_rows = [
        {"hearing_status": k, "adjudication": ADJUDICATION.get(k, "unknown"), "rows": v}
        for k, v in sorted(hearing.items(), key=lambda kv: -kv[1])
    ]
    xt = m["ecb_status_x_hearing"]
    ecb_disagree = {
        "active_but_dismissed": sum(
            r["n"]
            for r in xt
            if r["ecb_violation_status"] == "ACTIVE" and r["hearing_status"] == "DISMISSED"
        ),
        "resolve_but_pending": sum(
            r["n"]
            for r in xt
            if r["ecb_violation_status"] == "RESOLVE" and r["hearing_status"] == "PENDING"
        ),
    }
    out = {
        "status": "PROPOSED, not approved",
        "dob_disposition": rows,
        "dob_summary": summary,
        "ecb_adjudication": adj_rows,
        "ecb_status_x_hearing": xt,
        "ecb_disagreement": ecb_disagree,
        "ecb_certification_status": m["ecb_certification_status"],
        "ecb_severity": m["ecb_severity"],
    }
    (DOCS / "proposed_mappings.json").write_text(
        json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    md = [
        "# Proposed DOB disposition and ECB adjudication mappings (Task 5, step 1)",
        "",
        "Status: **proposed, not approved.** Generated by `backend/scripts/task5_mappings.py`"
        " from `step1_measurements.json`. Definitions are DOB's own text from `6v9u-ndjg`"
        " (last updated 2017), copied verbatim.",
        "",
        "## DOB disposition codes",
        "",
        "| Code | DOB definition (verbatim) | Outcome | Order event | Rows | ACTIVE | CLOSED |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda r: (r["outcome"], r["code"])):
        md.append(
            f"| {r['code']} | {r['dob_definition'] or '**not in the reference table**'} | "
            f"`{r['outcome']}` | {r['order_event'] or ''} | {r['rows_in_use']:,} | "
            f"{r['rows_by_status'].get('ACTIVE', 0):,} | {r['rows_by_status'].get('CLOSED', 0):,} |"
        )
    md += [
        "",
        "## ECB hearing status to adjudication",
        "",
        'The ECB dictionary defines `hearing_status` only as "Status of the hearing". The '
        "OATH dataset dictionary gives no value list. The mapping is ours.",
        "",
        "| hearing_status | Proposed adjudication | Rows |",
        "|---|---|---|",
    ]
    for r in adj_rows:
        md.append(f"| {r['hearing_status']} | `{r['adjudication']}` | {r['rows']:,} |")
    (DOCS / "proposed_mappings.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({"dob_summary": summary, "ecb_disagreement": ecb_disagree}, indent=1))


if __name__ == "__main__":
    main()
