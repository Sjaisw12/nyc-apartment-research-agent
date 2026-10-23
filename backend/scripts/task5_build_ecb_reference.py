"""Build the ECB reference file used by the adapter (Task 5, step 3).

    uv run python scripts/task5_build_ecb_reference.py

Reads the approved, committed sources and writes
src/nyc_apartment_agent/records/data/ecb_reference.json:

- docs/task5/proposed_mappings.json: hearing status -> adjudication (approved
  2026-10-23 06:22Z), with the documented uncertainty for each value;
- docs/task5/step1_measurements.json: the measured vocabularies (hearing status,
  certification status, severity, correction status, violation type);
- docs/task5/ecb_reference_measurements.json: the infraction code list, the allowlisted
  (code, section/law text) pairs and the aggravated-level vocabulary.

Nothing is typed by hand except the notes below, which only state what the sources do
and do not document. A test rebuilds the file from these sources and compares.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "task5"
OUT_FILE = (
    ROOT / "backend" / "src" / "nyc_apartment_agent" / "records" / "data" / "ecb_reference.json"
)

HEARING_DEFINITION = (
    'The ECB dictionary defines hearing_status only as "Status of the hearing" and gives no '
    "value list. The grouping is the project's, not DOB's or OATH's, and has not been "
    "checked against OATH records (validation part D is pending)."
)
# What the sources do NOT say, per value. Never a guess at the meaning.
ADJUDICATION_NOTES = {
    "IN VIOLATION": "Recorded as IN VIOLATION.",
    "CURED/IN-VIO": (
        "Recorded as CURED/IN-VIO. The IN-VIO suffix is read as in violation; the dataset "
        "does not define the CURED prefix."
    ),
    "STIPULATION/IN-VIO": (
        "Recorded as STIPULATION/IN-VIO. The IN-VIO suffix is read as in violation; the "
        "dataset does not define the STIPULATION prefix."
    ),
    "ADMIT/IN-VIO": (
        "Recorded as ADMIT/IN-VIO. The IN-VIO suffix is read as in violation; the dataset "
        "does not define the ADMIT prefix."
    ),
    "POP/IN-VIO": (
        "Recorded as POP/IN-VIO. The IN-VIO suffix is read as in violation; the meaning of "
        "POP is not documented."
    ),
    "DEFAULT": (
        "Recorded as DEFAULT. The meaning of DEFAULT is not documented in the ECB or OATH "
        "dataset dictionaries. It is grouped as upheld_by_default, kept apart from upheld, "
        "and is not presented as a decision on the merits."
    ),
    "WRITTEN OFF": (
        "Recorded as WRITTEN OFF. The meaning is not documented. It is not read as upheld "
        "or dismissed."
    ),
    "DISMISSED": "Recorded as DISMISSED.",
    "PENDING": "Recorded as PENDING.",
    "None": "No hearing status recorded.",
}
CORRECTION_STATUS = {
    "definition": (
        '"whether or not the violation has been corrected. This is the status of the '
        "violation with DOB, not the status of the hearing with OATH. ACTIVE - still needs "
        'to be addressed; RESOLVE - the issue was either fixed with DOB or dismissed by OATH"'
    ),
    "source": "ECB data dictionary DD_DOB_ECB_Violations_2019-07-10.xlsx",
    "values": {"ACTIVE": "open_with_dob", "RESOLVE": "resolved_with_dob"},
}
SEVERITY = {
    "Hazardous": {"severity": "hazardous", "documented": True},
    "Non-Hazardous": {"severity": "non_hazardous", "documented": True},
    "Unknown": {"severity": "unknown", "documented": True},
    "CLASS - 1": {"severity": "class_1", "documented": False},
    "CLASS - 2": {"severity": "class_2", "documented": False},
    "CLASS - 3": {"severity": "class_3", "documented": False},
}
SEVERITY_NOTE = (
    "The ECB dictionary lists Hazardous, Non-Hazardous and Unknown. CLASS - 1, 2 and 3 are "
    "in use but not documented there; they are shown as recorded and flagged."
)


def _vocab(counts: dict) -> list[str]:
    return sorted(k for k in counts if k != "None")


def build() -> dict:
    mapping = json.loads((DOCS / "proposed_mappings.json").read_text(encoding="utf-8"))
    step1 = json.loads((DOCS / "step1_measurements.json").read_text(encoding="utf-8"))
    meas = json.loads((DOCS / "ecb_reference_measurements.json").read_text(encoding="utf-8"))
    res = step1["results"]
    adjudication = {
        r["hearing_status"]: {
            "adjudication": r["adjudication"],
            "note": ADJUDICATION_NOTES[r["hearing_status"]],
        }
        for r in mapping["ecb_adjudication"]
    }
    pairs = meas["infraction_pairs"]
    return {
        "generated_by": "backend/scripts/task5_build_ecb_reference.py",
        "sources": {
            "adjudication": "docs/task5/proposed_mappings.json (approved 2026-10-23 06:22Z)",
            "vocabularies": {
                "file": "docs/task5/step1_measurements.json",
                "measured_at": step1["finished_at"],
            },
            "infractions": {
                "file": "docs/task5/ecb_reference_measurements.json",
                "measured_at": meas["measured_at"],
                "truth_headers": meas["truth_headers"],
                "rule": meas["rule"],
            },
        },
        "hearing_status_definition": HEARING_DEFINITION,
        "adjudication": dict(sorted(adjudication.items())),
        "correction_status": CORRECTION_STATUS,
        "severity": SEVERITY,
        "severity_note": SEVERITY_NOTE,
        "vocabularies": {
            "hearing_status": _vocab(res["ecb_hearing_status"]),
            "certification_status": _vocab(res["ecb_certification_status"]),
            "severity": _vocab(res["ecb_severity"]),
            "ecb_violation_status": _vocab(res["ecb_status"]),
            "violation_type": _vocab(res["ecb_violation_type"]),
            "aggravated_level": _vocab(meas["aggravated_level"]),
        },
        "infraction_codes": sorted({p["code"] for p in pairs if p["code"] is not None}),
        "infraction_texts": {p["code"]: p["text"] for p in pairs if p["allowlisted"]},
        "infraction_texts_withheld": {
            "code_or_text_missing": sum("code_or_text_missing" in p["reasons"] for p in pairs),
            "privacy_pattern": sorted(
                p["code"] for p in pairs if "privacy_pattern" in p["reasons"]
            ),
        },
    }


def main() -> None:
    OUT_FILE.write_text(json.dumps(build(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT_FILE}")


if __name__ == "__main__":
    main()
