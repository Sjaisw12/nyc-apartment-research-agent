"""Task 5 step 5, decision A: review 2020-to-present 311 vocabulary candidates.

    uv run python scripts/task5_review_311_history.py draft RAW.json OUT_DIR
    uv run python scripts/task5_review_311_history.py write RAW.json DECISIONS.json
    uv run python scripts/task5_review_311_history.py check RAW.json

RAW.json is the unreviewed output of task5_measure_311_history.py (outside the
repository, never committed). A candidate is a value seen at least 20 times among
2020-to-present rows (agencies other than HPD and DOB) that is not already on the step-4
allowlist. Frequency does not prove a value is free of personal information, so every
candidate goes through two gates:

1. Automatic screen (fail closed): the step-4 unit, floor, phone, email and honorific
   patterns, plus patterns for request-specific detail: a street address, a number of
   four or more digits, a date, a time, '#', a URL, a parenthesized or bracketed aside.
2. Manual review of every remaining candidate, recorded in
   docs/task5/sr311_history_review.json: admitted values verbatim, rejected values only
   as SHA-256 hashes with a reason code. Picklist fields (agency, complaint_type,
   descriptor, descriptor_2, location_type) are city-maintained categories;
   resolution_description is agency template text and is admitted only when it is
   generic boilerplate that names no person, place, business, vehicle, date or
   narrative about the reporter.

`draft` writes the screened candidates to OUT_DIR (outside the repository) for review.
`write` records the review: DECISIONS.json (outside the repository) lists the manual
rejections as {"field", "value", "reason"}; every other screened candidate is admitted.
Only hashes of rejected values reach the committed file.
`check` verifies that the committed review accounts for every candidate in RAW.json
(admitted, auto-rejected or manually rejected by hash) and that no admitted value fails
the automatic screen. It prints the field counts recorded in the review.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / "docs" / "task5" / "sr311_history_review.json"
BUILDER = ROOT / "backend" / "scripts" / "task5_build_311_reference.py"
FIELDS = ("complaint_type", "descriptor", "descriptor_2", "location_type", "resolution_description")
MIN_COUNT = 20

_UNIT_FLOOR = re.compile(
    r"\b(?:APTS?|APARTMENTS?|UNITS?)\s*(?:NO\.?|#)?\s*\d|\b(?:FLOOR|FLR|FL)\.?\s*#?\d"
    r"|\b\d{1,3}(?:ST|ND|RD|TH)\s+(?:FLOOR|FLR|FL)\b",
    re.IGNORECASE,
)
_PRIVATE = re.compile(r"@|\d{3}[-. )]+\d{3}[-. ]\d{4}|\b(?:MR|MRS|MS|MISS|DR)\.?\s+[A-Z]")
SCREENS = {
    "unit_or_floor": _UNIT_FLOOR,
    "contact_or_honorific": _PRIVATE,
    "street_address": re.compile(
        r"\b\d+[A-Z]?\s+(?:[NSEW]\.?\s+)?[A-Z0-9]+(?:\s+[A-Z0-9]+)?\s+"
        r"(?:ST|STREET|AVE|AVENUE|RD|ROAD|BLVD|BOULEVARD|PL|PLACE|DRIVE|LANE|LN|PKWY|"
        r"PARKWAY|TERRACE|CT|COURT|WAY)\b",
        re.IGNORECASE,
    ),
    "long_number": re.compile(r"\d{4,}"),
    "date": re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b"),
    "time": re.compile(r"\b\d{1,2}:\d{2}\b"),
    "hash_sign": re.compile(r"#"),
    "url": re.compile(r"https?://|www\.", re.IGNORECASE),
    "aside": re.compile(r"[\[\]{}]"),
}


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def screen(value: str) -> str | None:
    for reason, pattern in SCREENS.items():
        if pattern.search(value):
            return reason
    return None


def step4_reference() -> dict:
    """The step-4 allowlists, rebuilt from their committed sources (not the packaged file)."""
    spec = importlib.util.spec_from_file_location("task5_build_311_reference", BUILDER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.base()


def candidates(raw: dict, reference: dict) -> dict[str, dict[str, int]]:
    out = {}
    agencies = raw["fields"]["agency"]
    if agencies["status"] == "complete":
        out["agency"] = {
            v: n
            for v, n in agencies["values"].items()
            if n >= MIN_COUNT and v not in reference["agencies"] and v not in ("HPD", "DOB")
        }
    for field in FIELDS:
        f = raw["fields"][field]
        if f["status"] != "complete":
            continue
        allowed = set(reference["allowlists"][field])
        out[field] = {v: n for v, n in f["values"].items() if n >= MIN_COUNT and v not in allowed}
    return out


def draft(raw: dict, reference: dict, out_dir: Path) -> None:
    if ROOT in out_dir.resolve().parents or out_dir.resolve() == ROOT:
        raise SystemExit("write the draft outside the repository")
    out_dir.mkdir(parents=True, exist_ok=True)
    for field, values in candidates(raw, reference).items():
        rows = []
        for v, n in sorted(values.items(), key=lambda x: -x[1]):
            rows.append({"value": v, "rows": n, "auto_reject": screen(v)})
        (out_dir / f"{field}.json").write_text(
            json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(field, len(rows), "candidates,", sum(1 for r in rows if r["auto_reject"]), "auto")


def check(raw: dict, reference: dict) -> None:
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    assert review["raw_truth_header"] == raw["fields"]["agency"]["truth_header"]
    problems = []
    for field, values in candidates(raw, reference).items():
        r = review["fields"][field]
        admitted = set(r["admitted"])
        manual = {h for h, _ in r["rejected_manual"]}
        auto = {v for v in values if screen(v)}
        for v in values:
            if v in auto:
                if v in admitted:
                    problems.append((field, "admitted value fails the screen", sha(v)))
            elif v not in admitted and sha(v) not in manual:
                problems.append((field, "candidate not reviewed", sha(v)))
        stray = admitted - set(values)
        if stray:
            problems.append((field, f"{len(stray)} admitted values are not candidates", ""))
        counts = {
            "candidates": len(values),
            "auto_rejected": len(auto),
            "manually_rejected": len(manual & {sha(v) for v in values}),
            "admitted": len(admitted),
        }
        base_hashes = {
            sha(v)
            for v in (
                reference["agencies"] if field == "agency" else reference["allowlists"][field]
            )
        }
        for h, why in r["step4_withdrawn"]:
            if h not in base_hashes or why not in REASONS:
                problems.append((field, "withdrawn hash is not a step-4 value", h))
        if counts != r["counts"]:
            problems.append((field, f"counts differ: {counts} vs {r['counts']}", ""))
        print(field, counts)
    if problems:
        raise SystemExit(f"review incomplete: {problems[:10]}")
    print("review accounts for every candidate")


REASONS = {
    "third_party_social_services": "describes whether an individual accepted or refused "
    "outreach, housing or mental-health services",
    "names_business": "names a private business",
    "person_centered_outreach": "describes outreach directed at a reported individual "
    "(a team sent or referred to engage them, or whether they were found)",
    "individual_service_outcome": "describes a social-service contact with, or service "
    "delivered to, an individual (for example aging services or outreach assistance)",
    "third_party_arrest": "states that a person was arrested",
}
# Step-5 correction (after the 20:00Z acceptance): the same rules applied to the step-4
# lists. A step-4 value that a reviewer withdraws is listed by hash in `step4_withdrawn`;
# the builder removes it from the published reference, while base() still rebuilds the
# accepted step-4 file byte for byte.
NOTES = (
    "Picklist fields (agency, complaint_type, descriptor, descriptor_2, location_type) hold "
    "city-maintained categories and codes; every candidate was read. resolution_description "
    "holds agency template text; every candidate that passed the automatic screen was read "
    "in full. Admitted: generic agency outcomes that name no person, address, business, "
    "vehicle or date and tell no story about the reporter. Agency phone numbers and URLs in "
    "templates trip the contact and URL screens; those templates stay withheld rather than "
    "carving out exceptions. Mis-encoded characters (for example 'â') are kept verbatim as "
    "published."
)


def write(raw: dict, reference: dict, decisions: list[dict]) -> None:
    manual: dict[str, list] = {}
    withdrawn: dict[str, list] = {}
    for d in decisions:
        assert d["reason"] in REASONS, d["reason"]
        target = withdrawn if d.get("step4") else manual
        target.setdefault(d["field"], []).append((d["value"], d["reason"]))
    for field, items in withdrawn.items():
        listed = set(reference["agencies"] if field == "agency" else reference["allowlists"][field])
        assert all(v in listed for v, _ in items), field
    fields = {}
    for field, values in candidates(raw, reference).items():
        rejected = {v: why for v, why in manual.get(field, [])}
        assert set(rejected) <= set(values), field
        auto = {v: screen(v) for v in values if screen(v)}
        admitted = {
            v: n
            for v, n in sorted(values.items(), key=lambda x: (-x[1], x[0]))
            if v not in auto and v not in rejected
        }
        f = raw["fields"][field]
        fields[field] = {
            "measured": {
                k: f[k] for k in ("total_rows", "null_rows", "distinct_values", "truth_header")
            }
            | {"values_at_least_min_count": sum(1 for n in f["values"].values() if n >= MIN_COUNT)},
            "counts": {
                "candidates": len(values),
                "auto_rejected": len(auto),
                "manually_rejected": len(rejected),
                "admitted": len(admitted),
            },
            "auto_rejected_by_screen": dict(sorted(Counter(auto.values()).items())),
            "rejected_manual": sorted([sha(v), why] for v, why in rejected.items()),
            "step4_withdrawn": sorted([sha(v), why] for v, why in withdrawn.get(field, [])),
            "admitted": admitted,
        }
    review = {
        "generated_by": "backend/scripts/task5_review_311_history.py",
        "measurement_script": "backend/scripts/task5_measure_311_history.py",
        "dataset": raw["dataset"],
        "where": raw["where"],
        "min_count": raw["min_count"],
        "measured_at": raw["measured_at"],
        "raw_truth_header": raw["fields"]["agency"]["truth_header"],
        "candidate_rule": "seen at least min_count times over the period and not on the "
        "step-4 allowlist; agencies HPD and DOB never",
        "screens": {k: p.pattern for k, p in SCREENS.items()},
        "manual_reject_reasons": REASONS,
        "review_notes": NOTES,
        "fields": fields,
        "query_log": raw["query_log"],
    }
    REVIEW.write_text(json.dumps(review, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {REVIEW}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("draft", "write", "check"))
    ap.add_argument("raw", type=Path)
    ap.add_argument("out", type=Path, nargs="?")
    a = ap.parse_args()
    raw = json.loads(a.raw.read_text(encoding="utf-8"))
    for field, f in raw["fields"].items():
        if f["status"] != "complete":
            raise SystemExit(f"{field} is {f['status']}: not usable")
        if f["truth_header"] != raw["fields"]["agency"]["truth_header"]:
            raise SystemExit(f"{field} was measured on another data version")
    reference = step4_reference()
    if a.mode == "draft":
        draft(raw, reference, a.out)
    elif a.mode == "write":
        write(raw, reference, json.loads(a.out.read_text(encoding="utf-8")))
    else:
        check(raw, reference)


if __name__ == "__main__":
    main()
