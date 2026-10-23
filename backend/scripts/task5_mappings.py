"""Task 5, step 1: build the PROPOSED DOB disposition and ECB adjudication mappings.

    uv run python scripts/task5_mappings.py

Reads docs/task5/step1_measurements.json (from task5_measure.py) and writes
docs/task5/proposed_mappings.json and docs/task5/proposed_mappings.md. Proposed only:
Sanyam reviews them at the step-1 gate before any adapter uses them.

Definitions come from two official DOB sources, both copied verbatim:
- the 2021 PDF "BIS Complaint Disposition Codes" (Rev. 09/21), extracted to
  docs/task5/dob_disposition_codes_2021.json by task5_extract_dob_codes.py; preferred;
- the reference dataset 6v9u-ndjg (metadata: last updated 2017), used only for codes the
  PDF does not list.
A code with neither definition gets `unknown_code`; its meaning is never guessed. The
counts always come from the recorded step-1 measurements, not from a new snapshot.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs" / "task5"

# Outcome groups. Codes with an official definition that are not listed fall to
# `other_recorded`. The groups keep apart: a violation actually served, a violation
# issued (the text does not say served), a violation prepared but not yet served, a
# corrective-action request and its result, a completed inspection and its result, and
# an order event (ORDER_EVENTS below).
OUTCOMES: dict[str, tuple[str, ...]] = {
    # text says "Served"
    "violation_served": ("A1", "A2", "A4", "A5", "A8", "A9", "H4", "V3", "W1"),
    # text says "Issued" or "Written", not "Served"
    "violation_issued": (
        "A6",
        "H3",
        "RK",
        "R5",
        "MB",
        "MC",
        "MD",
        "ME",
        "MF",
        "MG",
        "MH",
        "MI",
        "MJ",
        "MK",
        "U3",
        "U6",
        "X2",
        "X7",
        "X8",
    ),
    "violation_prepared_not_served": ("B1", "B2"),
    "corrective_action_requested": ("AA",),
    "corrective_action_corrected": ("AB",),
    "corrective_action_not_corrected": ("AC",),
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
        "U4",
        "U5",
    ),
    "no_violation": ("I2", "MA", "R1", "R6", "WA", "WF", "X1", "U2"),
    "inspection_completed": ("I3",),
    "inspection_passed": ("V1",),
    "inspection_failed": ("V2",),
    "inspection_findings_under_review": ("J5",),
    "unsubstantiated": ("I1",),
    "no_access": ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "WB", "U1"),
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
        "EF",
        "EG",
        "EH",
        "EJ",
        "EK",
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
    "U4": "stop_work_full_issued",
    "U5": "stop_work_partial_issued",
}


def order_action(code: str, definition: str | None) -> str | None:
    """What the official text says happened to the order, taken from its own verb.

    The event label names the kind of order; this field keeps "served" apart from
    "issued" (or no verb at all). Neither says the order is in effect now.
    """
    event = ORDER_EVENTS.get(code)
    if event is None:
        return None
    if event.endswith("_noncompliance"):
        return "noncompliance_violation"
    t = (definition or "").upper()
    if "RESCINDED" in t:
        return "rescinded"
    if "PREVIOUSLY ISSUED" in t:
        return "previously_issued"
    if "SERVED" in t:
        return "served"
    if "ISSUED" in t:
        return "issued"
    return "not_stated"


# A complaint whose disposition is one of these outcomes is dispositioned; status ACTIVE
# with such a code is flagged status_fields_disagree (design 4.3).
FINAL = {"violation_served", "violation_issued", "no_violation", "unsubstantiated", "referred"}

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


def outcome(code: str | None, defined: set[str]) -> str:
    if code in (None, "None", ""):
        return "none"
    if code not in defined:
        return "unknown_code"
    for name, codes in OUTCOMES.items():
        if code in codes:
            return name
    return "other_recorded"


def _norm(text: str | None) -> str:
    t = (text or "").upper().replace("–", "-").replace("’", "'").replace("&", "AND")
    t = re.sub(r"\s+[FPN]{1,2}$", "", t.strip())  # 2017 trailing flag letters
    return re.sub(r"[^A-Z0-9]+", " ", t).strip()


def difference(old: str | None, new: str | None) -> str | None:
    if old is None or new is None:
        return None
    a, b = _norm(old), _norm(new)
    if a == b:
        return "same_after_normalization"
    if a.replace("ECB", "OATH") == b or a.replace("ECB", "OATH") == b.replace(
        " NOTICE OF VIOLATION", " NOV"
    ):
        return "ecb_renamed_oath"
    return "wording_differs"


def main() -> None:
    m = json.loads((DOCS / "step1_measurements.json").read_text(encoding="utf-8"))["results"]
    pdf_doc = json.loads((DOCS / "dob_disposition_codes_2021.json").read_text(encoding="utf-8"))
    pdf: dict[str, str] = pdf_doc["codes"]
    table: dict[str, str] = m["disposition_code_table"]
    defined = set(pdf) | set(table)
    in_use: dict[str, int] = m["dob_disposition_codes_in_use"]
    by_status: dict[str, dict[str, int]] = {}
    for r in m["dob_status_x_disposition"]:
        by_status.setdefault(str(r["disposition_code"]), {})[str(r["status"])] = r["n"]

    codes = sorted(defined | {c for c in in_use if c != "None"})
    rows = []
    for c in codes:
        o = outcome(c, defined)
        rows.append(
            {
                "code": c,
                "definition": pdf.get(c) or table.get(c),
                "definition_source": "dob_pdf_2021"
                if c in pdf
                else ("ref_6v9u_ndjg_2017" if c in table else None),
                "definition_2021_pdf": pdf.get(c),
                "definition_2017_table": table.get(c),
                "wording_comparison": difference(table.get(c), pdf.get(c)),
                "outcome": o,
                "order_event": ORDER_EVENTS.get(c),
                "order_action": order_action(c, pdf.get(c) or table.get(c)),
                "rows_in_use": in_use.get(c, 0),
                "rows_by_status": by_status.get(c, {}),
            }
        )
    disagree_active = sum(
        r["rows_by_status"].get("ACTIVE", 0) for r in rows if r["outcome"] in FINAL
    )
    unknown_before = m["dob_disposition_codes_not_in_table"]
    resolved = {c: n for c, n in unknown_before.items() if c in pdf}
    still = {c: n for c, n in unknown_before.items() if c not in pdf}
    total = sum(m["dob_status"].values())
    summary = {
        "complaints_total": total,
        "active_with_final_disposition": disagree_active,
        "active_with_final_disposition_by_code": {
            r["code"]: r["rows_by_status"]["ACTIVE"]
            for r in rows
            if r["outcome"] in FINAL and r["rows_by_status"].get("ACTIVE")
        },
        "closed_with_no_disposition": by_status.get("None", {}).get("CLOSED", 0),
        "active_with_no_disposition": by_status.get("None", {}).get("ACTIVE", 0),
        "codes_in_use": len([c for c in in_use if c != "None"]),
        "codes_in_use_not_in_2017_table": len(unknown_before),
        "rows_with_codes_not_in_2017_table": sum(unknown_before.values()),
        "resolved_by_2021_pdf": {
            "codes": len(resolved),
            "rows": sum(resolved.values()),
            "list": sorted(resolved),
        },
        "still_unknown": {"codes": len(still), "rows": sum(still.values()), "list": sorted(still)},
        "rows_by_outcome": {},
    }
    for r in rows:
        summary["rows_by_outcome"][r["outcome"]] = (
            summary["rows_by_outcome"].get(r["outcome"], 0) + r["rows_in_use"]
        )
    summary["rows_by_outcome"]["none"] = sum(by_status.get("None", {}).values())
    comparison = {
        "in_2017_table_not_in_2021_pdf": {
            c: {"definition_2017_table": table[c], "rows_in_use": in_use.get(c, 0)}
            for c in sorted(set(table) - set(pdf))
        },
        "in_2021_pdf_not_in_2017_table": sorted(set(pdf) - set(table)),
        "in_both_wording_differs": [
            {
                "code": r["code"],
                "definition_2017_table": r["definition_2017_table"],
                "definition_2021_pdf": r["definition_2021_pdf"],
            }
            for r in rows
            if r["wording_comparison"] == "wording_differs"
        ],
        "in_both_ecb_renamed_oath": sorted(
            r["code"] for r in rows if r["wording_comparison"] == "ecb_renamed_oath"
        ),
        "in_both_same_after_normalization": sum(
            r["wording_comparison"] == "same_after_normalization" for r in rows
        ),
        "in_2021_pdf_not_in_use": sorted(set(pdf) - set(in_use)),
    }

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
    sources = {
        "dob_pdf_2021": {
            k: pdf_doc[k]
            for k in ("source_url", "obtained", "sha256", "pdf_metadata", "revision_marks")
        },
        "ref_6v9u_ndjg_2017": {
            "dataset": "https://data.cityofnewyork.us/d/6v9u-ndjg",
            "note": "metadata says last updated 2017; data version header 8 Mar 2018",
        },
        "counts": "docs/task5/step1_measurements.json (2026-10-23 04:39Z); no new snapshot",
    }
    out = {
        "status": "PROPOSED, not approved",
        "sources": sources,
        "dob_disposition": rows,
        "dob_summary": summary,
        "dob_definition_comparison": comparison,
        "ecb_adjudication": adj_rows,
        "ecb_status_x_hearing": xt,
        "ecb_disagreement": ecb_disagree,
        "ecb_certification_status": m["ecb_certification_status"],
        "ecb_severity": m["ecb_severity"],
    }
    (DOCS / "proposed_mappings.json").write_text(
        json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    res, st = summary["resolved_by_2021_pdf"], summary["still_unknown"]
    md = [
        "# Proposed DOB disposition and ECB adjudication mappings (Task 5, step 1)",
        "",
        "Status: **proposed, not approved.** Generated by `backend/scripts/task5_mappings.py`.",
        "",
        "Sources:",
        "",
        f'- DOB PDF "BIS Complaint Disposition Codes", Rev. 09/21 ({pdf_doc["source_url"]}),'
        f" created {pdf_doc['pdf_metadata'].get('CreationDate')}. Copy downloaded by Sanyam and"
        f" supplied through the project on 2026-10-23 (nyc.gov is blocked from the cloud"
        f" environment). SHA-256 `{pdf_doc['sha256']}`. Preferred definition.",
        "- Reference dataset `6v9u-ndjg` (last updated 2017). Used only where the PDF has no"
        " entry.",
        "- Counts: the recorded step-1 measurements (2026-10-23 04:39Z), not a new snapshot.",
        "",
        "Definitions are copied verbatim. A code with no official definition stays `unknown_code`.",
        "",
        "## Coverage",
        "",
        f"- Codes in use but absent from the 2017 table: "
        f"{summary['codes_in_use_not_in_2017_table']} codes, "
        f"{summary['rows_with_codes_not_in_2017_table']:,} rows.",
        f"- Defined by the 2021 PDF: **{res['codes']} codes, {res['rows']:,} rows** "
        f"({', '.join(res['list'])}).",
        f"- Still without an official definition: **{st['codes']} codes, {st['rows']:,} rows** "
        f"({', '.join(st['list'])}).",
        "",
        f"- ACTIVE complaints whose code is a final outcome: "
        f"{summary['active_with_final_disposition']:,} ("
        + ", ".join(
            f"{c} {n:,}" for c, n in summary["active_with_final_disposition_by_code"].items()
        )
        + "). The status and disposition fields disagree; the adapter flags"
        " `status_fields_disagree` and never derives one from the other.",
        "",
        "## Rows by proposed outcome",
        "",
        "| Outcome | Rows |",
        "|---|---|",
    ]
    for k, v in sorted(summary["rows_by_outcome"].items(), key=lambda kv: -kv[1]):
        md.append(f"| `{k}` | {v:,} |")
    md += [
        "",
        "## Order events",
        "",
        "Recorded events only. No order event means an order is currently in effect.",
        "",
        "`order_action` is the verb in the official text: served, issued, rescinded,"
        " previously_issued, noncompliance_violation (a violation for disobeying the order),"
        " or not_stated.",
        "",
        "| Code | Official definition | Event | Action | Outcome | Rows |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        if r["order_event"]:
            md.append(
                f"| {r['code']} | {r['definition']} | `{r['order_event']}` | "
                f"{r['order_action']} | `{r['outcome']}` | {r['rows_in_use']:,} |"
            )
    md += [
        "",
        "## DOB disposition codes",
        "",
        "| Code | Official definition (verbatim) | Source | Outcome | Order event "
        "| Rows | ACTIVE | CLOSED |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda r: (r["outcome"], r["code"])):
        src = {"dob_pdf_2021": "PDF 2021", "ref_6v9u_ndjg_2017": "table 2017"}.get(
            r["definition_source"], "none"
        )
        md.append(
            f"| {r['code']} | {r['definition'] or '**no official definition found**'} | {src} | "
            f"`{r['outcome']}` | {r['order_event'] or ''} | {r['rows_in_use']:,} | "
            f"{r['rows_by_status'].get('ACTIVE', 0):,} | {r['rows_by_status'].get('CLOSED', 0):,} |"
        )
    md += [
        "",
        "## 2017 table versus 2021 PDF",
        "",
        f"- Same wording after normalization (case, dashes, the 2017 trailing flag letters F,"
        f" P, N): {comparison['in_both_same_after_normalization']} codes.",
        f'- "ECB" renamed "OATH": {", ".join(comparison["in_both_ecb_renamed_oath"]) or "none"}.',
        f"- In the 2021 PDF only: {', '.join(comparison['in_2021_pdf_not_in_2017_table'])}.",
        "",
        "In the 2017 table only (kept, with the 2017 definition):",
        "",
        "| Code | 2017 definition | Rows in use |",
        "|---|---|---|",
    ]
    for c, v in comparison["in_2017_table_not_in_2021_pdf"].items():
        md.append(f"| {c} | {v['definition_2017_table']} | {v['rows_in_use']:,} |")
    md += [
        "",
        "Wording differs (both kept; the 2021 text is used):",
        "",
        "| Code | 2017 table | 2021 PDF |",
        "|---|---|---|",
    ]
    for d in comparison["in_both_wording_differs"]:
        md.append(f"| {d['code']} | {d['definition_2017_table']} | {d['definition_2021_pdf']} |")
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
    print(
        json.dumps(
            {
                "dob_summary": summary,
                "comparison_counts": {
                    k: (len(v) if not isinstance(v, int) else v) for k, v in comparison.items()
                },
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
