"""Task 5 step 6, Part C: DOB BIS / DOB NOW manual-validation checklist.

    cd backend && PYTHONPATH=. uv run python scripts/task5_validation_part_c.py

Replays the committed recordings (unchanged) for the five acceptance addresses and writes
docs/task5/validation/part_c_dob_checklist.json and .md for Sanyam to complete against
DOB's own systems (BIS and DOB NOW are not reachable from the cloud environment).

What is listed, per BIN with DOB complaints in the recording:
- every complaint recorded as ACTIVE;
- an independent sample of up to 10 CLOSED complaints, chosen by a fixed hash of the
  complaint number (SHA-256 of "task5-part-c-sample" + NUL + number, lowest first), so the
  choice does not depend on any outcome, attribution or date;
- every complaint whose disposition is a stop-work or vacate order event (our wording
  is checked against DOB's), whether or not it is in the sample.

Each row carries what we recorded (number, BIN, status, disposition code and dates, the
source's data version) and the expected attribution, with blank columns for the lookup.
Nothing here is a validation result: Part C stays PENDING until lookup evidence is
supplied and checked.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
OUT = ROOT / "docs" / "task5" / "validation"
SALT = b"task5-part-c-sample\x00"
SAMPLE = 10
BIS_BY_BIN = (
    "https://a810-bisweb.nyc.gov/bisweb/ComplaintsByAddressServlet?requestid=2&allbin={bin}"
)
DOB_NOW = "https://a810-dobnow.nyc.gov/publish/Index.html#!/"


def sample_key(complaint_number: str) -> str:
    return hashlib.sha256(SALT + complaint_number.encode("utf-8")).hexdigest()


def select(records: list) -> list[tuple[object, list[str]]]:
    """(record, reasons) for every listed complaint, in a stable order."""
    by_bin: dict[str, list] = {}
    for r in records:
        by_bin.setdefault(r.source_bin or "", []).append(r)
    out = []
    for _bin, rows in sorted(by_bin.items()):
        closed = sorted(
            (r for r in rows if r.complaint_status.value == "closed"),
            key=lambda r: sample_key(r.complaint_number),
        )
        sampled = {r.complaint_number for r in closed[:SAMPLE]}
        for r in sorted(rows, key=lambda r: r.complaint_number):
            why = []
            if r.complaint_status.value == "active":
                why.append("active")
            if r.complaint_number in sampled:
                why.append("closed_sample")
            if r.order_event is not None:
                why.append("order_event")
            if r.complaint_status.value not in ("active", "closed"):
                why.append("status_" + r.complaint_status.value)
            if why:
                out.append((r, why))
    return out


def _row(address_key: str, address: str, r, why: list[str]) -> dict:
    ev = r.order_event
    return {
        "address_key": address_key,
        "address": address,
        "complaint_number": r.complaint_number,
        "bin": r.source_bin,
        "listed_because": why,
        "recorded_status": r.status_raw,
        "disposition_code": r.disposition_code,
        "disposition_description": r.disposition_description,
        "received_date": r.received_date.raw,
        "disposition_date": r.disposition_date.raw,
        "inspection_date": r.inspection_date.raw,
        "complaint_category": r.complaint_category,
        "expected_attribution": r.attribution.value,
        "attribution_basis": r.attribution_basis.value,
        "order_event": None
        if ev is None
        else {"kind": ev.kind.value, "action": ev.action.value, "our_statement": ev.statement},
        "source_data_as_of": r.data_as_of,
        "source_dataset": r.dataset_id,
        "bis_lookup": BIS_BY_BIN.format(bin=r.source_bin),
    }


def build() -> dict:
    sys.path.insert(0, str(BACKEND))
    from tests.db import task5_recorded as rec

    addresses = []
    rows = []
    for key in rec.ADDRESSES:
        r = rec.build(key)
        res = r.dob
        bins = sorted({x.source_bin for x in res.records if x.source_bin})
        addresses.append(
            {
                "address_key": key,
                "address": r.address,
                "resolved_bbl": res.resolved_bbl,
                "resolved_bin": res.resolved_bin,
                "dob_result_status": res.status.value,
                "dob_records": len(res.records),
                "bins_with_complaints": bins,
                "data_as_of": res.provenance.data_as_of,
                "note": None
                if res.records
                else "No DOB complaints were requested (tax-lot scope, unsupported_join). "
                "Look up the lot's current BIN only to note coverage; nothing is compared.",
            }
        )
        for x, why in select(res.records):
            rows.append(_row(key, r.address, x, why))
    return {
        "generated_by": "backend/scripts/task5_validation_part_c.py",
        "status": "PENDING: manual lookup evidence not yet supplied",
        "recording": "backend/tests/fixtures/dob311/dob_complaints_cassette.json (2026-10-23)",
        "selection_rule": (
            f"every ACTIVE complaint; up to {SAMPLE} CLOSED per BIN, lowest SHA-256 of "
            "'task5-part-c-sample' + NUL + complaint number; every order-event complaint"
        ),
        "official_sources": {"bis_by_bin": BIS_BY_BIN, "dob_now": DOB_NOW},
        "addresses": addresses,
        "rows": rows,
    }


def markdown(data: dict) -> str:
    lines = [
        "# Part C: DOB BIS / DOB NOW manual-validation checklist",
        "",
        "**Status: PENDING.** Nothing here is validated until the lookup columns are filled",
        "in from DOB's own systems and checked. Generated by",
        "`backend/scripts/task5_validation_part_c.py` from the committed recording; a rerun",
        "gives the same file.",
        "",
        "## How to check",
        "",
        "1. Write down the date and time of your lookup.",
        "2. Open DOB BIS complaints by BIN (link in each table header). If BIS does not list",
        f"   a complaint, search DOB NOW ({DOB_NOW}) by BIN or complaint number.",
        "3. For each row, record BIS's status (ACTIVE or CLOSED), disposition code, and",
        "   disposition date, and whether BIS shows the complaint at that BIN.",
        "4. For rows marked `order_event`, copy BIS's wording for the disposition and say",
        "   whether BIS shows the order as currently in effect. We never claim it is.",
        "5. Differences from data timing are expected: our data version is shown per",
        "   address. Note BIS's own 'as of' date if it shows one.",
        "",
        f"Selection: {data['selection_rule']}.",
        "",
    ]
    for a in data["addresses"]:
        lines += [
            f"## {a['address']}",
            "",
            f"BBL {a['resolved_bbl']}, resolved BIN {a['resolved_bin'] or 'none'}; DOB result "
            f"`{a['dob_result_status']}`, {a['dob_records']} complaints; data version "
            f"{a['data_as_of'] or 'n/a'}.",
            "",
        ]
        if a["note"]:
            lines += [a["note"], ""]
            continue
        for b in a["bins_with_complaints"]:
            rows = [r for r in data["rows"] if r["address_key"] == a["address_key"]]
            rows = [r for r in rows if r["bin"] == b]
            attr = sorted({r["expected_attribution"] for r in rows})
            lines += [
                f"### BIN {b} (expected: {', '.join(attr)}) - [BIS]({BIS_BY_BIN.format(bin=b)})",
                "",
                "| Complaint | Listed because | Our status | Disposition | Received | "
                "Disposition date | BIS status | BIS disposition | BIS date | At this BIN? "
                "| Match? |",
                "|---|---|---|---|---|---|---|---|---|---|---|",
            ]
            for r in rows:
                lines.append(
                    f"| {r['complaint_number']} | {', '.join(r['listed_because'])} | "
                    f"{r['recorded_status']} | {r['disposition_code'] or ''} | "
                    f"{r['received_date'] or ''} | {r['disposition_date'] or ''} | | | | | |"
                )
            orders = [r for r in rows if r["order_event"]]
            if orders:
                lines += ["", "Order wording to compare:", ""]
                for r in orders:
                    ev = r["order_event"]
                    lines.append(
                        f"- {r['complaint_number']} ({r['disposition_code']}): our label "
                        f"`{ev['kind']}` / `{ev['action']}`. DOB text: "
                        f'"{r["disposition_description"]}". BIS wording: ____ ; '
                        "shown as in effect? ____"
                    )
            lines.append("")
    lines += [
        "## Reconciliation (after results arrive)",
        "",
        "| Complaint | Difference | Explanation (timing / mapping / attribution / error) |",
        "|---|---|---|",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = build()
    (OUT / "part_c_dob_checklist.json").write_text(
        json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (OUT / "part_c_dob_checklist.md").write_text(markdown(data), encoding="utf-8")
    counts: dict[str, int] = {}
    for r in data["rows"]:
        for w in r["listed_because"]:
            counts[w] = counts.get(w, 0) + 1
    print(len(data["rows"]), "rows", counts)


if __name__ == "__main__":
    main()
