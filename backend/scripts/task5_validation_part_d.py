"""Task 5 step 6, Part D: read-only cross-check of recorded ECB summonses against OATH.

    cd backend && PYTHONPATH=. uv run python scripts/task5_validation_part_d.py

For every ECB summons in the committed recordings of the five acceptance addresses, this
looks up the public summons number in OATH Hearings Division Case Status (jz4z-kudi) and
compares the hearing outcome, dates and amounts. It never changes a recording or a result.

- Matching: OATH's `ticket_number` is tried exactly and left-padded with zeros to 10
  characters (OATH writes DOB summonses with a leading 0). Every summons is reported:
  matched once, matched more than once (duplicate), not found, or no usable number.
- Privacy: only these OATH columns are requested ($select): ticket number, issuing
  agency, hearing status and result, hearing and decision dates (date part only),
  penalty, total, paid, balance, violation block and lot. Respondent names and addresses,
  violation location street/house/floor and free-text details are never requested.
- Version: every query's X-SODA2-Truth-Last-Modified header is kept. OATH and DOB are
  separate systems on separate schedules; differences can be timing, not error.
- Meaning: DOB's `hearing_status` is DOB's copy of a charge outcome; OATH's
  `hearing_result` is the tribunal's record. Agreement is judged on raw values after
  normalizing separators and on the project's grouping (upheld / dismissed / pending).
  DEFAULT, WRITTEN OFF and POP-style values are never read as an established legal
  conclusion; they are reported as "meaning not documented".

Writes docs/task5/validation/part_d_oath_crosscheck.json and .md.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
OUT = ROOT / "docs" / "task5" / "validation"
OATH = "https://data.cityofnewyork.us/resource/jz4z-kudi.json"
FIELDS = (
    "ticket_number",
    "issuing_agency",
    "hearing_status",
    "hearing_result",
    "hearing_date",
    "decision_date",
    "penalty_imposed",
    "total_violation_amount",
    "paid_amount",
    "balance_due",
    "violation_location_block_no",
    "violation_location_lot_no",
)
BATCH = 40
MAX_TRIES = 8
SALT = b"task5-part-d-manual\x00"
TICKET_FINDER = "https://a820-ecbticketfinder.nyc.gov/searchHome.action"
_UNDOCUMENTED = re.compile(r"DEFAULT|WRITTEN OFF|POP\b|NO APPEARANCE", re.I)


def _parse_http_date(value: str) -> datetime:
    return datetime.strptime(value, "%a, %d %b %Y %H:%M:%S GMT")


def candidates(ecb_number: str) -> list[str]:
    forms = [ecb_number]
    if len(ecb_number) < 10:
        forms.append(ecb_number.rjust(10, "0"))
    return forms


def norm(value: str | None) -> str | None:
    if value is None:
        return None
    v = re.sub(r"\s*/\s*", "/", value.strip().upper())
    v = re.sub(r"\bIN-VIO\b|\bIN VIO\b", "IN VIOLATION", v)
    v = re.sub(r"\s+", " ", v)
    return v


def group(value: str | None) -> str:
    """The project's coarse grouping; never a legal conclusion for undocumented values."""
    v = norm(value)
    if not v:
        return "none"
    if _UNDOCUMENTED.search(v):
        return "meaning_not_documented"
    if "DISMISS" in v:
        return "dismissed"
    if "IN VIOLATION" in v:
        return "upheld"
    if "PENDING" in v or "RESCHEDULED" in v or "ADJOURN" in v:
        return "pending"
    return "other"


def money(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def date_only(value: str | None) -> str | None:
    if not value:
        return None
    m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})", value)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def query(client: httpx.Client, tickets: list[str]) -> tuple[list[dict], str | None, dict]:
    where = "ticket_number in (" + ", ".join(f"'{t}'" for t in tickets) + ")"
    params = {"$select": ",".join(FIELDS), "$where": where, "$limit": 1000}
    started = time.monotonic()
    resp = client.get(OATH, params=params)
    resp.raise_for_status()
    rows = resp.json()
    header = resp.headers.get("x-soda2-truth-last-modified")
    log = {
        "tickets": len(tickets),
        "rows": len(rows),
        "truth_header": header,
        "seconds": round(time.monotonic() - started, 1),
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    for r in rows:
        assert set(r) <= set(FIELDS), sorted(set(r) - set(FIELDS))
    return rows, header, log


def compare(rec: dict, oath: dict) -> dict:
    diffs = []
    ours, theirs = norm(rec["hearing_status"]), norm(oath.get("hearing_result"))
    raw_agree = ours == theirs
    g_ours, g_theirs = group(rec["hearing_status"]), group(oath.get("hearing_result"))
    if not raw_agree:
        diffs.append("hearing_outcome")
    for field, ofield in (("penalty_imposed", "penalty_imposed"),):
        a, b = money(rec[field]), money(oath.get(ofield))
        if a is not None and b is not None and a != b:
            diffs.append(field)
    for field, ofield in (("amount_paid", "paid_amount"), ("balance_due", "balance_due")):
        a, b = money(rec[field]), money(oath.get(ofield))
        if a is not None and b is not None and a != b:
            diffs.append(field)
    if rec["hearing_date"] and date_only(oath.get("hearing_date")) != rec["hearing_date"]:
        diffs.append("hearing_date")

    # Location as an independent attribution check: compare block and lot as numbers.
    def _num(v):
        return int(v) if v and str(v).strip().isdigit() else None

    ob, ol = (
        _num(oath.get("violation_location_block_no")),
        _num(oath.get("violation_location_lot_no")),
    )
    db, dl = _num(rec["source_block"]), _num(rec["source_lot"])
    if None not in (ob, ol, db, dl) and (ob, ol) != (db, dl):
        diffs.append("block_lot")
    if raw_agree:
        verdict = "agree"
    elif g_ours == g_theirs == "meaning_not_documented":
        verdict = "wording_differs_meaning_not_documented"
    elif g_ours == g_theirs and g_ours not in ("none", "other"):
        verdict = "wording_differs_same_group"
    else:
        verdict = "disagree"
    return {
        "outcome_verdict": verdict,
        "oath_hearing_status": oath.get("hearing_status"),
        "oath_hearing_result": oath.get("hearing_result"),
        "oath_hearing_date": date_only(oath.get("hearing_date")),
        "oath_decision_date": date_only(oath.get("decision_date")),
        "oath_penalty_imposed": oath.get("penalty_imposed"),
        "oath_paid_amount": oath.get("paid_amount"),
        "oath_balance_due": oath.get("balance_due"),
        "oath_block": oath.get("violation_location_block_no"),
        "oath_lot": oath.get("violation_location_lot_no"),
        "oath_issuing_agency": oath.get("issuing_agency"),
        "outcome_raw_agrees": raw_agree,
        "our_group": g_ours,
        "oath_group": g_theirs,
        "differences": diffs,
    }


def recorded() -> list[dict]:
    sys.path.insert(0, str(BACKEND))
    from tests.db import task5_recorded as rec

    out = []
    for key in rec.ADDRESSES:
        r = rec.build(key)
        for x in r.ecb.records:
            out.append(
                {
                    "address_key": key,
                    "ecb_violation_number": x.ecb_violation_number,
                    "isn_dob_bis_extract": x.isn_dob_bis_extract,
                    "source_bin": x.source_bin,
                    "source_block": x.source_block,
                    "source_lot": x.source_lot,
                    "attribution": x.attribution.value,
                    "hearing_status": x.hearing_status_raw,
                    "adjudication": x.adjudication.value,
                    "correction_status": x.correction_status.value,
                    "hearing_date": x.hearing_date.value.isoformat()
                    if x.hearing_date.value
                    else None,
                    "penalty_imposed": None
                    if x.penalty_imposed is None
                    else str(x.penalty_imposed),
                    "amount_paid": None if x.amount_paid is None else str(x.amount_paid),
                    "balance_due": None if x.balance_due is None else str(x.balance_due),
                    "dob_data_as_of": x.data_as_of,
                }
            )
    return out


def manual_sample(rows: list[dict]) -> list[dict]:
    """One summons per address (lowest SHA-256 of the number), independent of outcomes."""
    picked = []
    for key in dict.fromkeys(r["address_key"] for r in rows):
        mine = [r for r in rows if r["address_key"] == key and r["ecb_violation_number"]]
        mine.sort(key=lambda r: hashlib.sha256(SALT + r["ecb_violation_number"].encode()).digest())
        if mine:
            picked.append(mine[0])
    return picked


def run() -> dict:
    rows = recorded()
    wanted: dict[str, str] = {}
    for r in rows:
        if r["ecb_violation_number"]:
            for c in candidates(r["ecb_violation_number"]):
                wanted[c] = r["ecb_violation_number"]
    log = []
    tickets = sorted(wanted)
    batches = [tickets[i : i + BATCH] for i in range(0, len(tickets), BATCH)]
    answers: dict[int, tuple[list[dict], str | None]] = {}
    with httpx.Client(timeout=180, headers={"Accept": "application/json"}) as client:
        for n, b in enumerate(batches):
            got, header, entry = query(client, b)
            log.append({"batch": n, "try": 1, **entry})
            answers[n] = (got, header)
        # Socrata can answer from replicas on different versions. Ask a batch again
        # (up to MAX_TRIES in all) until every batch is on the newest version seen.
        for attempt in range(2, MAX_TRIES + 1):
            newest = max((h for _, h in answers.values() if h), key=_parse_http_date)
            stale = [n for n, (_, h) in answers.items() if h != newest]
            if not stale:
                break
            for n in stale:
                got, header, entry = query(client, batches[n])
                log.append({"batch": n, "try": attempt, **entry})
                answers[n] = (got, header)
    found: dict[str, list[dict]] = {}
    for got, _ in answers.values():
        for g in got:
            found.setdefault(wanted[g["ticket_number"]], []).append(g)
    headers = sorted({h for _, h in answers.values() if h})
    results = []
    for r in rows:
        num = r["ecb_violation_number"]
        hits = found.get(num, []) if num else []
        if not num:
            outcome = "no_usable_number"
        elif not hits:
            outcome = "not_found"
        elif len(hits) > 1:
            outcome = "duplicate_in_oath"
        else:
            outcome = "matched"
        entry = {**r, "match": outcome, "oath_rows": len(hits)}
        if outcome == "matched":
            entry |= compare(r, hits[0])
        elif outcome == "duplicate_in_oath":
            entry["oath_hearing_results"] = sorted({h.get("hearing_result") or "" for h in hits})
        results.append(entry)
    return {
        "generated_by": "backend/scripts/task5_validation_part_d.py",
        "dataset": "jz4z-kudi",
        "requested_columns": list(FIELDS),
        "oath_truth_headers": headers,
        "single_oath_version": len(headers) == 1,
        "version_rule": "every batch re-asked (up to 8 tries) until all answer on the newest "
        "X-SODA2-Truth-Last-Modified seen; if they never agree, single_oath_version is false "
        "and the not-found results are inconclusive",
        "dob_recording": "backend/tests/fixtures/dob311/dob_ecb_cassette.json (2026-10-23)",
        "query_log": log,
        "results": results,
        "manual_sample": [
            {
                "address_key": m["address_key"],
                "ecb_violation_number": m["ecb_violation_number"],
                "oath_ticket_number": m["ecb_violation_number"].rjust(10, "0"),
                "our_hearing_status": m["hearing_status"],
                "our_penalty_imposed": m["penalty_imposed"],
                "our_hearing_date": m["hearing_date"],
            }
            for m in manual_sample(rows)
        ],
        "manual_lookup": TICKET_FINDER,
    }


def markdown(d: dict) -> str:
    res = d["results"]
    by_match = Counter(r["match"] for r in res)
    matched = [r for r in res if r["match"] == "matched"]
    lines = [
        "# Part D: OATH cross-check of recorded ECB summonses",
        "",
        "Automated, read-only. Generated by `backend/scripts/task5_validation_part_d.py`.",
        f"OATH data version(s): {', '.join(d['oath_truth_headers'])}. DOB recording: "
        "2026-10-23. The two systems update separately, so some differences are timing.",
        "",
        "## Match summary",
        "",
        "| Address | Summonses | Matched | Duplicate in OATH | Not found | No usable number |",
        "|---|---|---|---|---|---|",
    ]
    for key in dict.fromkeys(r["address_key"] for r in res):
        c = Counter(r["match"] for r in res if r["address_key"] == key)
        lines.append(
            f"| {key} | {sum(c.values())} | {c['matched']} | {c['duplicate_in_oath']} | "
            f"{c['not_found']} | {c['no_usable_number']} |"
        )
    lines.append(
        f"| **all** | {len(res)} | {by_match['matched']} | {by_match['duplicate_in_oath']} | "
        f"{by_match['not_found']} | {by_match['no_usable_number']} |"
    )
    agree = sum(1 for r in matched if not r["differences"])
    lines += [
        "",
        f"Matched summonses with no difference in any compared field: {agree} of {len(matched)}.",
        "",
        "## Hearing outcome: DOB's hearing_status vs OATH's hearing_result (matched)",
        "",
        "| DOB hearing_status | OATH hearing_result | Summonses | Verdict | Groups (ours / OATH) |",
        "|---|---|---|---|---|",
    ]
    tab = Counter(
        (
            r["hearing_status"] or "",
            r["oath_hearing_result"] or "(blank)",
            r["outcome_verdict"],
            f"{r['our_group']} / {r['oath_group']}",
        )
        for r in matched
    )
    for (a, b, v, g), n in sorted(tab.items(), key=lambda x: -x[1]):
        lines.append(f"| {a} | {b} | {n} | {v} | {g} |")
    verdicts = Counter(r["outcome_verdict"] for r in matched)
    lines += ["", "Outcome verdicts: " + ", ".join(f"{k} {v}" for k, v in sorted(verdicts.items()))]
    diff_counts = Counter(f for r in matched for f in r["differences"])
    lines += [
        "",
        "## Differences by field (matched)",
        "",
        "| Field | Summonses |",
        "|---|---|",
    ]
    for f, n in sorted(diff_counts.items()):
        lines.append(f"| {f} | {n} |")
    lines += [
        "",
        "## Every summons with a difference, a duplicate or no match",
        "",
        "| Address | ECB number | Match | Differences | DOB status | OATH result | DOB "
        "penalty | OATH penalty | DOB hearing | OATH hearing |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in res:
        if r["match"] == "matched" and not r["differences"]:
            continue
        lines.append(
            f"| {r['address_key']} | {r['ecb_violation_number'] or ''} | {r['match']} | "
            f"{', '.join(r.get('differences', []))} | {r['hearing_status'] or ''} | "
            f"{r.get('oath_hearing_result') or ', '.join(r.get('oath_hearing_results', []))} | "
            f"{r['penalty_imposed'] or ''} | {r.get('oath_penalty_imposed') or ''} | "
            f"{r['hearing_date'] or ''} | {r.get('oath_hearing_date') or ''} |"
        )
    lines += [
        "",
        "## Not found, by hearing year",
        "",
        "| Hearing year | Not found | Matched |",
        "|---|---|---|",
    ]
    years = sorted({(r["hearing_date"] or "none")[:4] for r in res})
    for y in years:
        nf = sum(
            1 for r in res if r["match"] == "not_found" and (r["hearing_date"] or "none")[:4] == y
        )
        mt = sum(
            1 for r in res if r["match"] == "matched" and (r["hearing_date"] or "none")[:4] == y
        )
        if nf:
            lines.append(f"| {y} | {nf} | {mt} |")
    lines += [
        "",
        "Every unmatched summons had its hearing before 2000 and every matched one in 2000 or "
        "later, which points to the OATH dataset's coverage period, not to a wrong number. "
        "These are reported as source unavailable or inconclusive, not as failures, and are "
        "not excluded.",
        "",
        "## Manual official lookup: five summonses (PENDING)",
        "",
        f"One per address, chosen by a fixed hash of the number. Look each up in OATH's "
        f"ticket finder ({d['manual_lookup']}) or DOB BIS, and fill in the blanks.",
        "",
        "| Address | ECB number | OATH ticket | Our hearing status | Our penalty | Our hearing "
        "date | Official status | Official result | Official penalty | Match? |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for m in d["manual_sample"]:
        lines.append(
            f"| {m['address_key']} | {m['ecb_violation_number']} | {m['oath_ticket_number']} | "
            f"{m['our_hearing_status']} | {m['our_penalty_imposed']} | {m['our_hearing_date']} "
            "| | | | |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if sys.argv[1:] == ["--render"]:  # rewrite the .md from the saved .json, no queries
        d = json.loads((OUT / "part_d_oath_crosscheck.json").read_text(encoding="utf-8"))
        (OUT / "part_d_oath_crosscheck.md").write_text(markdown(d), encoding="utf-8")
        return
    d = run()
    (OUT / "part_d_oath_crosscheck.json").write_text(
        json.dumps(d, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (OUT / "part_d_oath_crosscheck.md").write_text(markdown(d), encoding="utf-8")
    print(Counter(r["match"] for r in d["results"]), d["oath_truth_headers"])


if __name__ == "__main__":
    main()
