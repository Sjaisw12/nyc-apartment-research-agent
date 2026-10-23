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
REVIEW = ROOT / "docs" / "task5" / "sr311_history_review.json"
REVIEW_SCRIPT = ROOT / "backend" / "scripts" / "task5_review_311_history.py"
# Pinned so that any change to the allowlists is deliberate and reviewed.
REFERENCE_SHA256 = "2f513ee0cc92ad5d9540639d4128ccc07be1627a089139ebcd27bfd7e09033cc"
REVIEW_SHA256 = "fe5a55163758721ff9468333d3204edacbfd82f286077a3dbda30624f9bb6d71"
# The step-4 file, before decision A; base() must still rebuild it byte for byte.
STEP4_SHA256 = "6b2a60553863a09a0f9b72726384aacb2577c644f63fceb964d16b6530ddd532"
DICTIONARY_SHA256 = "f45d7c2c89ab0c31aec760e112a4b89c279af363cfd5ee33c192cedb8561a256"
WORKBOOK_SHA256 = "6cc30e13eb41ee7c2f3c4e6b1078df1065ecf97513b551911b3271189d2e94fc"


def _module(path=SCRIPT, name="task5_build_311_reference"):
    spec = importlib.util.spec_from_file_location(name, path)
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
    assert hashlib.sha256(REVIEW.read_bytes()).hexdigest() == REVIEW_SHA256
    assert ref()["sources"]["dictionary"]["sha256"] == WORKBOOK_SHA256


def test_provenance_of_the_snapshot_is_recorded():
    r = ref()
    assert r["generated_by"] == "backend/scripts/task5_build_311_reference.py"
    assert "not exhaustive" in r["rule"] and "reviewed" in r["rule"]
    hist = r["sources"]["history_review"]
    assert hist["file"] == "docs/task5/sr311_history_review.json"
    assert hist["truth_header"] == "Fri, 23 Oct 2026 01:38:29 GMT"
    assert "frequency alone admits nothing" in hist["rule"]
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
        "complaint_type": (300, 0),
        "descriptor": (1296, 0),
        "descriptor_2": (1029, 0),
        "location_type": (200, 0),
        "resolution_description": (704, 22),
    }


def test_step4_base_is_unchanged():
    step4 = json.dumps(_module().base(), indent=1, ensure_ascii=False) + "\n"
    assert hashlib.sha256(step4.encode("utf-8")).hexdigest() == STEP4_SHA256
    sizes = {f: len(v) for f, v in _module().base()["allowlists"].items()}
    assert sizes == {
        "complaint_type": 265,
        "descriptor": 993,
        "descriptor_2": 788,
        "location_type": 192,
        "resolution_description": 246,
    }


def review():
    return json.loads(REVIEW.read_text(encoding="utf-8"))


def test_history_review_provenance_and_counts():
    r = review()
    assert r["dataset"] == "erm2-nwe9" and r["min_count"] == 20
    assert "2020" in r["where"] and "agency not in ('HPD', 'DOB')" in r["where"]
    assert r["raw_truth_header"] == "Fri, 23 Oct 2026 01:38:29 GMT"
    # 6 fields x 7 years, each answered once on the same data version and under its
    # limit; one timed-out attempt (complaint_type, 2026) is logged and was asked again.
    failed = [q for q in r["query_log"] if "error" in q]
    answered = [q for q in r["query_log"] if "error" not in q]
    assert [(q["field"], q["year"], q["error"]) for q in failed] == [
        ("complaint_type", 2026, "SourceTimeoutError")
    ]
    assert sorted((q["field"], q["year"]) for q in answered) == sorted(
        (f, y) for f in r["fields"] for y in range(2020, 2027)
    )
    for q in answered:
        assert q["truth_header"] == r["raw_truth_header"]
        assert q["rows"] < q["params"]["$limit"]
        assert set(q["params"]) == {"$select", "$where", "$group", "$limit"}
    counts = {f: tuple(v["counts"].values()) for f, v in r["fields"].items()}
    assert counts == {  # candidates, auto rejected, manually rejected, admitted
        "agency": (6, 0, 0, 6),
        "complaint_type": (35, 0, 0, 35),
        "descriptor": (303, 0, 0, 303),
        "descriptor_2": (241, 0, 0, 241),
        "location_type": (8, 0, 0, 8),
        "resolution_description": (559, 96, 5, 458),
    }
    for f, v in r["fields"].items():
        c = v["counts"]
        assert c["candidates"] == c["auto_rejected"] + c["manually_rejected"] + c["admitted"]
        assert (
            len(v["admitted"]) == c["admitted"]
            and len(v["rejected_manual"]) == c["manually_rejected"]
        )
        assert sum(v["auto_rejected_by_screen"].values()) == c["auto_rejected"]
        assert all(n >= 20 for n in v["admitted"].values()), f


def test_history_admissions_pass_every_screen_and_are_in_the_reference():
    screens = _module(REVIEW_SCRIPT, "task5_review_311_history")
    builder = _module()
    packaged = ref()
    for field, v in review()["fields"].items():
        allowed = set(packaged["agencies"] if field == "agency" else packaged["allowlists"][field])
        for value in v["admitted"]:
            assert screens.screen(value) is None, (field, value)
            assert not builder._private(value), (field, value)
            assert value in allowed
        assert not {"HPD", "DOB"} & set(v["admitted"])


def test_rejected_history_values_are_hashes_only():
    r = review()
    text = REVIEW.read_text(encoding="utf-8")
    reasons = set(r["manual_reject_reasons"])
    for v in r["fields"].values():
        for digest, reason in v["rejected_manual"]:
            assert re.fullmatch(r"[0-9a-f]{64}", digest) and reason in reasons
    # No unreviewed grouped text is committed: nothing outside the admitted values can
    # carry a phone number, email, URL beyond the screen patterns, or a unit or floor.
    admitted = {a for v in r["fields"].values() for a in v["admitted"]}
    rest = json.loads(text)
    for v in rest["fields"].values():
        v.pop("admitted")
    rest.pop("screens")
    blob = json.dumps(rest, ensure_ascii=False)
    assert not re.search(r"@|\d{3}[-. )]+\d{3}[-. ]\d{4}|https?://|www\.", blob)
    assert admitted  # the admitted values are the only grouped text in the file


def test_known_sensitive_and_contact_templates_stay_withheld():
    mod = _module()
    allowed = set(ref()["allowlists"]["resolution_description"])
    for value in (
        "The person has refused services from the mobile outreach response team.",
        "The individual was found and outreach assistance was offered.",
        "Your complaint was referred to the vendor, CityBridge.",
        "Please call (212) 639-9675 for more information.",
    ):
        assert value not in allowed
    from nyc_apartment_agent.records.sr311 import public_value
    from nyc_apartment_agent.records.sr311_models import WITHHELD_VALUE

    assert public_value("resolution_description", "Call 212-639-9675.") == (WITHHELD_VALUE, True)
    assert mod._private("Call 212-639-9675.")


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
