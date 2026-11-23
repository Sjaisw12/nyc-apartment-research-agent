"""The adapter's DOB reference file is exactly what the approved sources produce."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "backend" / "scripts" / "task5_build_dob_reference.py"
PACKAGED = (
    ROOT
    / "backend"
    / "src"
    / "nyc_apartment_agent"
    / "records"
    / "data"
    / "dob_complaints_reference.json"
)


def _build():
    spec = importlib.util.spec_from_file_location("task5_build_dob_reference", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build()


def test_packaged_reference_matches_the_approved_mapping_and_category_list():
    assert json.loads(PACKAGED.read_text(encoding="utf-8")) == _build()


def test_reference_records_its_sources():
    ref = json.loads(PACKAGED.read_text(encoding="utf-8"))
    assert ref["sources"]["dispositions"]["pdf_sha256"] == (
        "7ee93140b24d56fec2bacddc3c5f6037ab3b377c7d112440c2a322f3efc636ba"
    )
    assert ref["sources"]["categories"]["pdf_sha256"] == (
        "e8751c6c1119f3424bdcc274b989459a226db3a3726f6ce817135baf8fa0974d"
    )
    assert len(ref["categories"]) == 97
    assert set(ref["final_outcomes"]) == {
        "violation_served",
        "violation_issued",
        "no_violation",
        "unsubstantiated",
        "referred",
    }


def test_category_list_coverage_is_recorded_from_the_measurements():
    cats = json.loads((ROOT / "docs" / "task5" / "dob_complaint_categories_2018.json").read_text())
    cov = cats["coverage"]
    assert (cov["codes_in_use"], cov["rows"]) == (197, 3145939)
    assert (cov["codes_in_use_not_listed"], cov["rows_with_codes_not_listed"]) == (100, 477755)
