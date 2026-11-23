"""Task 5, step 1: the committed DOB and 311 cassettes are sanitized and unmodified.

These fixtures are public (they are in git). Checks:
- each cassette's integrity hash still matches (load_cassette raises otherwise);
- 311 rows carry unique_key = [WITHHELD] and a fixture pseudonym, and no address as
  typed, street, coordinates, location or channel; timestamps keep only the date;
- 311 resolution text carries no unit or floor detail;
- ECB rows carry no respondent field or free-text description;
- no 311 query URL names a unique_key or a Socrata row id.
"""

import json
import re
from pathlib import Path

import pytest

from nyc_apartment_agent.cassette import load_cassette
from nyc_apartment_agent.records.redaction import WITHHELD, needs_redaction

FIXTURES = Path(__file__).parent / "fixtures" / "dob311"
SR311_WITHHELD = (
    "incident_address",
    "street_name",
    "cross_street_1",
    "cross_street_2",
    "intersection_street_1",
    "intersection_street_2",
    "landmark",
    "latitude",
    "longitude",
    "x_coordinate_state_plane",
    "y_coordinate_state_plane",
    "location",
    "open_data_channel_type",
)
ECB_WITHHELD = (
    "respondent_name",
    "respondent_house_number",
    "respondent_street",
    "respondent_city",
    "respondent_zip",
    "violation_description",
)
PSEUDONYM = re.compile(r"^FX\d{6}$")


def _rows(name: str) -> list[tuple[str, list[dict]]]:
    entries = load_cassette(FIXTURES / f"{name}_cassette.json")
    out = []
    for url, entry in entries.items():
        body = json.loads(entry["body"])
        if isinstance(body, list):
            out.append((url, body))
    return out


@pytest.mark.parametrize(
    "name", ["dob_complaints", "dob_ecb", "sr311", "footprints", "hpd_buildings"]
)
def test_cassette_integrity(name):
    assert load_cassette(FIXTURES / f"{name}_cassette.json")


def test_311_rows_are_sanitized():
    keys = []
    for url, rows in _rows("sr311"):
        assert "unique_key%3D" not in url and ":id" not in url and "%3Aid" not in url
        for row in rows:
            if "unique_key" not in row:
                continue  # an aggregate (count) response
            assert row["unique_key"] == WITHHELD
            keys.append(row["fixture_row_ref"])
            assert PSEUDONYM.match(row["fixture_row_ref"])
            for f in SR311_WITHHELD:
                assert row.get(f) in (None, WITHHELD), f
            for f in ("created_date", "closed_date", "due_date", "resolution_action_updated_date"):
                v = row.get(f)
                assert v is None or v.endswith("T00:00:00.000"), f
            assert not needs_redaction(row.get("resolution_description"))
    assert keys and len(keys) == len(set(keys))


def test_ecb_rows_withhold_respondent_and_free_text():
    seen = 0
    for _, rows in _rows("dob_ecb"):
        for row in rows:
            if "isn_dob_bis_extract" not in row:
                continue
            seen += 1
            for f in ECB_WITHHELD:
                assert row.get(f) in (None, WITHHELD), f
    assert seen


def test_meta_lists_each_address_and_no_raw_311_key():
    meta = (FIXTURES / "cassettes.meta.json").read_text(encoding="utf-8")
    data = json.loads(meta)
    assert set(data["addresses"]) == {
        "west_farms_1134",
        "west_17_220",
        "west_farms_1217",
        "fifth_ave_350",
        "acp_1845",
    }
    assert 'unique_key":' not in meta
