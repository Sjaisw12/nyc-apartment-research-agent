"""Recognizing the adjudication grouping an ECB snapshot was saved under (offline)."""

import copy
import json
from pathlib import Path

import pytest

from nyc_apartment_agent.records.ecb_compat import (
    UnrecognizedAdjudicationError,
    read_stored,
    stored_version,
)
from nyc_apartment_agent.records.ecb_models import WITHHELD_VALUE, EcbViolationResult
from nyc_apartment_agent.records.ecb_violations import STATEMENT

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "dob311" / "ecb_snapshots_2026-10-23.json"
)
LEGACY = json.loads(FIXTURE.read_text("utf-8"))["snapshots"]


def payload(key):
    s = LEGACY[key]
    p = copy.deepcopy(s["result_payload"])
    p["records"] = [copy.deepcopy(r["payload"]) for r in s["records"]]
    p["provenance"]["pages"] = []
    return p


def test_old_snapshots_are_recognized_and_read_without_change_to_the_input():
    for key in LEGACY:
        p = payload(key)
        before = copy.deepcopy(p)
        assert stored_version(p) == "2026-10-23"
        out, reading = read_stored(p)
        assert p == before  # the stored JSON is never modified
        assert reading.rederived and reading.recorded["records"]
        EcbViolationResult.model_validate(out)


def test_values_identical_in_both_groupings_read_as_current():
    p = payload("west_farms_1134")
    p["records"] = [r for r in p["records"] if r["hearing_status_raw"] == "DISMISSED"]
    p["summary"] = None
    assert stored_version(p) == "2026-10-28"
    out, reading = read_stored(p)
    assert out is p and not reading.rederived


def test_a_withheld_value_is_unknown_in_every_grouping():
    p = payload("west_farms_1134")
    p["summary"] = None
    p["records"] = [
        r | {"hearing_status_raw": WITHHELD_VALUE, "adjudication": "unknown"}
        for r in p["records"][:1]
    ]
    assert stored_version(p) == "2026-10-28"
    p["records"][0]["adjudication"] = "upheld"
    with pytest.raises(UnrecognizedAdjudicationError):
        stored_version(p)


def test_an_old_statement_alone_marks_an_old_snapshot():
    p = payload("west_farms_1134")
    p["records"] = []
    assert stored_version(p) == "2026-10-23"
    p["summary"]["statement"] = STATEMENT
    assert stored_version(p) == "2026-10-28"
    p["summary"]["statement"] = "edited"
    with pytest.raises(UnrecognizedAdjudicationError):
        stored_version(p)
