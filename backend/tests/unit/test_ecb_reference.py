"""The adapter's ECB reference file is exactly what the approved sources produce."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "backend" / "scripts" / "task5_build_ecb_reference.py"
PACKAGED = (
    ROOT / "backend" / "src" / "nyc_apartment_agent" / "records" / "data" / "ecb_reference.json"
)


def _build():
    spec = importlib.util.spec_from_file_location("task5_build_ecb_reference", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build()


def ref():
    return json.loads(PACKAGED.read_text(encoding="utf-8"))


def test_packaged_reference_matches_the_approved_sources():
    assert ref() == _build()


def test_adjudication_mapping_is_the_approved_one_with_its_uncertainty():
    adj = {k: v["adjudication"] for k, v in ref()["adjudication"].items()}
    assert adj == {
        "ADMIT/IN-VIO": "upheld",
        "CURED/IN-VIO": "upheld",
        "DEFAULT": "upheld_by_default",
        "DISMISSED": "dismissed",
        "IN VIOLATION": "upheld",
        "None": "unknown",
        "PENDING": "pending",
        "POP/IN-VIO": "upheld",
        "STIPULATION/IN-VIO": "upheld",
        "WRITTEN OFF": "other",
    }
    notes = {k: v["note"] for k, v in ref()["adjudication"].items()}
    for value in ("DEFAULT", "POP/IN-VIO", "WRITTEN OFF"):
        assert "not documented" in notes[value]
    assert "the project's" in ref()["hearing_status_definition"]


def test_infraction_allowlist_counts_are_the_measured_ones():
    r = ref()
    meas = json.loads((ROOT / "docs" / "task5" / "ecb_reference_measurements.json").read_text())
    assert (meas["pairs"], meas["allowlisted"], meas["distinct_codes"]) == (1011, 898, 1011)
    assert meas["single_version"] and meas["truth_headers"] == ["Thu, 22 Oct 2026 17:04:42 GMT"]
    assert len(r["infraction_texts"]) == 898 and len(r["infraction_codes"]) == 1011
    assert r["infraction_texts_withheld"] == {
        "code_or_text_missing": 108,
        "privacy_pattern": ["B8C", "BG7", "BK1", "BN8", "BR1"],
    }


def test_vocabularies_are_the_measured_ones():
    v = ref()["vocabularies"]
    assert len(v["violation_type"]) == 15 and len(v["hearing_status"]) == 9
    assert len(v["certification_status"]) == 9 and len(v["severity"]) == 6
    assert v["aggravated_level"] == [
        "AGGRAVATED OFFENSE LEVEL 1",
        "AGGRAVATED OFFENSE LEVEL 2",
        "MULTIPLE OFFENSE",
        "NO",
    ]
