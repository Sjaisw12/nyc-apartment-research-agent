"""No committed fixture may carry apartment or floor detail (approved decision D2).

Scans every JSON file under tests/fixtures, including bodies stored inside cassettes.
"""

import json
from pathlib import Path

import pytest

from nyc_apartment_agent.records.redaction import (
    CONTACT_ALWAYS_WITHHELD,
    CONTACT_INDIVIDUAL_EXTRA,
    CONTACT_PERSON_FIELDS,
    MARKER,
    TEXT_FIELDS,
    UNIT_FIELDS,
    WITHHELD,
    WITHHELD_FIELDS,
    needs_redaction,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _rows(obj):
    """Yield every dict that looks like a source row, wherever it is nested."""
    if isinstance(obj, dict):
        if "body" in obj and isinstance(obj["body"], str):
            try:
                yield from _rows(json.loads(obj["body"]))
            except ValueError:
                pass
        yield obj
        for v in obj.values():
            yield from _rows(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _rows(v)


def test_no_unit_detail_in_any_fixture():
    problems = []
    files = sorted(FIXTURES.rglob("*.json"))
    assert files
    for path in files:
        for row in _rows(json.loads(path.read_text(encoding="utf-8"))):
            for f in UNIT_FIELDS:
                if f in row and row[f] not in (None, "", MARKER):
                    problems.append(f"{path.name}: {f}")
            for f in TEXT_FIELDS:
                if isinstance(row.get(f), str) and needs_redaction(row[f]):
                    problems.append(f"{path.name}: {f} still has unit/floor detail")
            for f in WITHHELD_FIELDS:  # anonymity flag, 311 request number
                if f in row and row[f] not in (None, "", WITHHELD):
                    problems.append(f"{path.name}: {f} not withheld")
            if "registrationcontactid" in row:  # HPD registration contact (D11)
                for f in CONTACT_ALWAYS_WITHHELD:
                    if row.get(f) not in (None, "", WITHHELD):
                        problems.append(f"{path.name}: contact {f} not withheld")
                if any(row.get(f) == WITHHELD for f in CONTACT_PERSON_FIELDS):
                    for f in CONTACT_INDIVIDUAL_EXTRA:
                        if row.get(f) not in (None, "", WITHHELD):
                            problems.append(f"{path.name}: {f} of a named person not withheld")
    assert problems == []


@pytest.mark.parametrize(
    ("name", "dataset"),
    [
        ("violations_cassette.json", "wvxf-dwi5"),
        ("complaints_cassette.json", "ygpa-z7cr"),
        ("contacts_cassette.json", "feu5-w2e2"),
    ],
)
def test_redacted_fixtures_record_the_original_hash(name, dataset):
    meta = json.loads((FIXTURES / "hpd" / name).read_text("utf-8"))
    # Only data pages carry a body hash; the /api/views/ catalog entry holds no rows.
    hpd_pages = [
        e
        for u, e in meta["entries"].items()
        if f"/resource/{dataset}.json" in u and "count" not in u
    ]
    assert hpd_pages and all("original_body_sha256" in e for e in hpd_pages)
    assert all("redaction_events" in e for e in hpd_pages)
