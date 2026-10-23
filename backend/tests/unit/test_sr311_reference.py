"""The 311 reference file is exactly what the committed sources produce (design 18.2).

The allowlists are a reproducible snapshot, not an exhaustive or permanent list: they
record their window, source versions, generator and hashes, and a rebuild from the same
committed inputs must give the same file byte for byte.
"""

import hashlib
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "backend" / "scripts" / "task5_build_311_reference.py"
PACKAGED = (
    ROOT / "backend" / "src" / "nyc_apartment_agent" / "records" / "data" / "sr311_reference.json"
)
DICTIONARY = ROOT / "docs" / "task5" / "sr311_dictionary_values.json"
# Pinned so that any change to the allowlists is deliberate and reviewed.
REFERENCE_SHA256 = "6b2a60553863a09a0f9b72726384aacb2577c644f63fceb964d16b6530ddd532"
DICTIONARY_SHA256 = "f45d7c2c89ab0c31aec760e112a4b89c279af363cfd5ee33c192cedb8561a256"
WORKBOOK_SHA256 = "6cc30e13eb41ee7c2f3c4e6b1078df1065ecf97513b551911b3271189d2e94fc"


def _module():
    spec = importlib.util.spec_from_file_location("task5_build_311_reference", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ref():
    return json.loads(PACKAGED.read_text(encoding="utf-8"))


def test_packaged_reference_is_rebuilt_byte_for_byte():
    built = json.dumps(_module().build(), indent=1, ensure_ascii=False) + "\n"
    assert built == PACKAGED.read_text(encoding="utf-8")


def test_file_hashes_are_pinned():
    assert hashlib.sha256(PACKAGED.read_bytes()).hexdigest() == REFERENCE_SHA256
    assert hashlib.sha256(DICTIONARY.read_bytes()).hexdigest() == DICTIONARY_SHA256
    assert ref()["sources"]["dictionary"]["sha256"] == WORKBOOK_SHA256


def test_provenance_of_the_snapshot_is_recorded():
    r = ref()
    assert r["generated_by"] == "backend/scripts/task5_build_311_reference.py"
    assert "not exhaustive" in r["rule"]
    freq = r["sources"]["frequencies"]
    assert freq["window"].startswith("created_date >= 2026-09-01")
    assert freq["min_count"] == 20
    for field, stats in r["field_stats"].items():
        q = stats["source_query"]
        assert q["truth_header"] and q["where"] and q["at"], field


def test_allowlists_hold_no_private_pattern():
    mod = _module()
    for field, values in ref()["allowlists"].items():
        for v in values:
            assert not mod._private(v), (field, v)
            assert not re.search(r"\d{3}[-. ]\d{3}[-. ]\d{4}", v)


def test_allowlist_sizes_and_exclusions_are_the_measured_ones():
    stats = {
        f: (s["allowlisted"], s["excluded_privacy_pattern"])
        for f, s in ref()["field_stats"].items()
    }
    assert stats == {
        "complaint_type": (265, 0),
        "descriptor": (993, 0),
        "descriptor_2": (788, 0),
        "location_type": (192, 0),
        "resolution_description": (246, 22),
    }


def test_agencies_exclude_hpd_and_dob_and_status_mapping_is_the_approved_one():
    r = ref()
    assert "HPD" not in r["agencies"] and "DOB" not in r["agencies"]
    assert r["status"] == {
        "Open": "open",
        "In Progress": "open",
        "Assigned": "open",
        "Pending": "open",
        "Started": "open",
        "Closed": "closed",
        "Canceled": "canceled",
    }
    assert r["status_values_measured"] == [
        "Assigned",
        "Closed",
        "In Progress",
        "Open",
        "Pending",
        "Unspecified",
    ]
    assert "does not show that a condition was corrected" in r["status_note"]
