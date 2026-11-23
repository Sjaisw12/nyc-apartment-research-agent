"""Task 5 step 6: the validation package is reproducible and privacy safe.

- Part C (DOB checklist) is rebuilt offline from the recordings, byte for byte.
- Part D (OATH) and Part E (311 aggregates) came from live queries; their committed JSON
  is checked for completeness and privacy, and their Markdown re-renders exactly.
- Matching and grouping rules are unit tested: an undocumented OATH value (DEFAULT,
  WRITTEN OFF) is never grouped as an established outcome.
"""

import importlib.util
import json
import re
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "backend" / "scripts"
OUT = ROOT / "docs" / "task5" / "validation"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def part_c():
    return _load("task5_validation_part_c")


@pytest.fixture(scope="module")
def part_d():
    return _load("task5_validation_part_d")


@pytest.fixture(scope="module")
def part_e():
    return _load("task5_validation_part_e")


def _json(name):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


# ---- Part C ---------------------------------------------------------------------------


def test_part_c_checklist_is_rebuilt_byte_for_byte(part_c):
    data = part_c.build()
    assert json.dumps(data, indent=1, ensure_ascii=False) + "\n" == (
        OUT / "part_c_dob_checklist.json"
    ).read_text(encoding="utf-8")
    assert part_c.markdown(data) == (OUT / "part_c_dob_checklist.md").read_text(encoding="utf-8")


def test_part_c_selection_rule(part_c):
    data = _json("part_c_dob_checklist.json")
    assert data["status"].startswith("PENDING")
    rows = data["rows"]
    reasons = Counter(w for r in rows for w in r["listed_because"])
    assert reasons == {"active": 4, "closed_sample": 71, "order_event": 22}
    assert len(rows) == 91
    per_bin = Counter(r["bin"] for r in rows if "closed_sample" in r["listed_because"])
    assert max(per_bin.values()) == 10
    # every ACTIVE complaint of the recording is listed
    assert {r["complaint_number"] for r in rows if r["recorded_status"] == "ACTIVE"} == {
        r["complaint_number"] for r in rows if "active" in r["listed_because"]
    }
    # the sample key ignores everything but the number
    assert part_c.sample_key("123") == part_c.sample_key("123") != part_c.sample_key("124")
    tax_lot = [a for a in data["addresses"] if a["dob_result_status"] == "unsupported_join"]
    assert len(tax_lot) == 1 and tax_lot[0]["note"]


def test_part_c_holds_no_private_field():
    text = (OUT / "part_c_dob_checklist.json").read_text(encoding="utf-8")
    for word in ("recorded_address", "dob_unit", "zip", "respondent", "house_street"):
        assert word not in text


# ---- Part D ---------------------------------------------------------------------------


def test_oath_matching_and_grouping_rules(part_d):
    assert part_d.candidates("39132924H") == ["39132924H", "039132924H"]
    assert part_d.candidates("0391329241") == ["0391329241"]
    assert part_d.norm("CURED/IN-VIO") == "CURED/IN VIOLATION"
    assert part_d.group("IN VIOLATION") == "upheld"
    assert part_d.group("DISMISSED") == "dismissed"
    for undocumented in ("DEFAULT", "DEFAULTED", "WRITTEN OFF", "Default/ No Appearance"):
        assert part_d.group(undocumented) == "meaning_not_documented"
    assert part_d.group("STIPULATED") == "other"
    assert part_d.group(None) == "none"


def test_oath_compare_never_hides_a_difference(part_d):
    rec = {
        "hearing_status": "IN VIOLATION",
        "penalty_imposed": "100",
        "amount_paid": "0",
        "balance_due": "100",
        "hearing_date": "2020-01-02",
        "source_block": "00835",
        "source_lot": "0041",
    }
    oath = {
        "hearing_result": "DISMISSED",
        "penalty_imposed": "0",
        "paid_amount": "0",
        "balance_due": "100",
        "hearing_date": "2020-01-03T00:00:00.000",
        "violation_location_block_no": "00835",
        "violation_location_lot_no": "00041",
    }
    out = part_d.compare(rec, oath)
    assert out["outcome_verdict"] == "disagree"
    assert out["differences"] == ["hearing_outcome", "penalty_imposed", "hearing_date"]
    same = part_d.compare(
        rec | {"hearing_status": "DEFAULT"}, oath | {"hearing_result": "DEFAULTED"}
    )
    assert same["outcome_verdict"] == "wording_differs_meaning_not_documented"


def test_part_d_accounts_for_every_summons_on_one_version(part_d):
    d = _json("part_d_oath_crosscheck.json")
    assert d["requested_columns"] == list(part_d.FIELDS)
    assert d["single_oath_version"] is True
    res = d["results"]
    assert len(res) == 312  # 3 + 4 + 12 + 241 + 52, the accepted ECB totals
    assert Counter(r["match"] for r in res) == {"matched": 223, "not_found": 89}
    assert all((r["hearing_date"] or "") < "2000" for r in res if r["match"] == "not_found")
    assert len(d["manual_sample"]) == 5
    assert part_d.markdown(d) == (OUT / "part_d_oath_crosscheck.md").read_text(encoding="utf-8")


def test_part_d_holds_no_respondent_or_location_text():
    text = (OUT / "part_d_oath_crosscheck.json").read_text(encoding="utf-8")
    for word in (
        "respondent",
        "violation_location_house",
        "violation_location_street",
        "violation_details",
        "violation_description",
        "violation_location_floor",
    ):
        assert word not in text


# ---- Part E ---------------------------------------------------------------------------


def test_part_e_agrees_with_the_adapter_and_renders_exactly(part_e):
    d = _json("part_e_311_aggregates.json")
    for a in d["addresses"]:
        assert a["independent_answer_stable"] and a["unchanged_across_adapter_run"]
        assert a["independent"]["retained_total"] == a["live_adapter"]["retained_total"]
        assert a["live_adapter"]["agency_month_differences"] == []
        assert a["independent_lot_family"] == sorted(a["adapter_lot_family"])
    assert part_e.markdown(d) == (OUT / "part_e_311_aggregates.md").read_text(encoding="utf-8")


def test_part_e_writes_no_311_key():
    for name in ("part_e_311_aggregates.json", "part_e_311_aggregates.md"):
        text = (OUT / name).read_text(encoding="utf-8")
        assert "unique_key in" not in text
        # 311 keys are 8 to 9 digits; BINs are 7 and BBLs 10, so nothing in between
        assert not re.search(r"(?<!\d)\d{8,9}(?!\d)", text), name
    d = _json("part_e_311_aggregates.json")
    for q in d["query_log"]:
        assert "unique_key in" not in json.dumps(q["params"])


def test_part_e_diff_helper(part_e):
    a = {"DSNY": {"2024-01": 2}, "NYPD": {"2024-02": 1}}
    b = {"DSNY": {"2024-01": 1}}
    assert part_e.diff(a, b) == [
        {"agency": "DSNY", "month": "2024-01", "independent": 2, "other": 1},
        {"agency": "NYPD", "month": "2024-02", "independent": 1, "other": 0},
    ]
