"""Unit/floor redaction. Inputs are real description shapes seen in wvxf-dwi5."""

import pytest

from nyc_apartment_agent.records.redaction import (
    MARKER,
    canonical_sha256,
    needs_redaction,
    redact_row,
    redact_text,
)

CASES = [
    (
        "HEAT IN THE ENTIRE APARTMENT LOCATED AT APT 1H, 2nd STORY, 1st APARTMENT FROM WEST "
        "AT NORTH , SECTION 2",
        ["1H", "2nd STORY", "FROM WEST"],
    ),
    ("PROOF OF COMPLIANCE FOR APTS 3A, 4B & 5C", ["3A", "4B", "5C"]),
    ("SPRINKER HEAD IN KITCHEN 2 STY APT 12. , SECTION", ["2 STY", "APT 12"]),
    ("APT #4C and APT.5 and APT NO. 7 and UNIT PH2 on FLOOR 3", ["4C", "APT.5", "7", "PH2", "3"]),
]


@pytest.mark.parametrize(("text", "gone"), CASES)
def test_unit_and_floor_detail_is_removed(text, gone):
    out, n = redact_text(text)
    assert n > 0
    for fragment in gone:
        assert fragment not in out
    assert not needs_redaction(out)  # idempotent: nothing left to redact


@pytest.mark.parametrize(
    "text",
    [
        "§ 27-2142 ADM CODE APTS HAVE BEEN VACATED BY THIS DEPARTMENT",
        "LOCKED DOOR TO BOILER ROOM AT BASEMENT",
        "SELFCLOSING DOORS AT PUB HALL APT ENTRANCES AT EAST & WEST.",
        "REPAIR THE BROKEN OR DEFECTIVE 4578G \\ HHN96276",
    ],
)
def test_text_without_unit_ids_is_untouched(text):
    assert redact_text(text) == (text, 0)


def test_redact_row_masks_fields_and_reports_them():
    row = {"violationid": "1", "apartment": "1H", "story": "2", "novdescription": "AT APT 1H"}
    out, fields = redact_row(row)
    assert out["apartment"] == MARKER and out["story"] == MARKER
    assert "1H" not in out["novdescription"]
    assert fields == ["apartment", "story", "novdescription"]
    assert row["apartment"] == "1H"  # original untouched
    assert canonical_sha256(row) != canonical_sha256(out)
    assert redact_row(out) == (out, [])  # already redacted: no-op
