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
and do not document, and the 2026-10-28 revision of the grouping (ADJUDICATION_REVISION),
which keeps the approved value beside the revised one. A test rebuilds the file from these
sources and compares.
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
    "value list. The grouping is the project's, not DOB's or OATH's. It was revised on "
    "2026-10-28 after the step 6 OATH cross-check (validation part D): only IN VIOLATION is "
    "grouped as upheld. Stipulation, admission, cure, default and POP outcomes are kept "
    "apart, because they are not shown to be a hearing officer's decision on the merits."
)
# OATH documentation consulted for the 2026-10-28 revision. nyc.gov and the NYC rules
# library could not be fetched from the build environment; the wording was read in web
# search excerpts of these pages and still needs a manual check against the pages.
OATH_SOURCES = {
    "dataset_dictionary": (
        "OATH Hearings Division Case Status (jz4z-kudi) data dictionary: 'Hearing Result | "
        "Outcome or result of the hearing'; no value is defined"
    ),
    "stipulation": [
        "https://www.nyc.gov/site/buildings/safety/oath-hearings-penalties.page",
        "https://www.nyc.gov/assets/oath/downloads/pdf/FR_ModelRulesAdoption_63016.pdf",
    ],
    "admit_and_pay_and_cure": [
        "https://www.nyc.gov/site/buildings/safety/oath-hearings-penalties.page",
        "https://www.nyc.gov/site/buildings/property-or-business-owner/oath-frequently-asked-questions.page",
    ],
    "default": [
        "https://codelibrary.amlegal.com/codes/newyorkcity/latest/NYCrules/0-0-0-83943",
        "https://codelibrary.amlegal.com/codes/newyorkcity/latest/NYCrules/0-0-0-84148",
        "https://www.nyc.gov/site/oath/hearings/missed-hearings-default-frequently-asked-questions.page",
    ],
    "verification": (
        "Read in web search excerpts on 2026-10-28; the pages were not reachable from the "
        "build environment and have not been checked in full"
    ),
}
# The approved 2026-10-23 grouping, revised on 2026-10-28 (Sanyam's step 6 decision).
# The approved value is kept beside the revised one in the reference file.
ADJUDICATION_REVISION = {
    "STIPULATION/IN-VIO": "in_violation_by_stipulation",
    "ADMIT/IN-VIO": "in_violation_by_admission",
    "CURED/IN-VIO": "in_violation_cured",
    "POP/IN-VIO": "in_violation_basis_not_documented",
    "DEFAULT": "in_violation_by_default",
}
_NOT_A_DECISION = " It is not presented as a hearing officer's decision after a contested hearing."
# What the sources say and do NOT say, per value. Never a guess at the meaning.
ADJUDICATION_NOTES = {
    "IN VIOLATION": (
        "Recorded as IN VIOLATION, read as OATH's finding that the violation occurred. The "
        "dataset does not say how the hearing was held or whether the charge was contested."
    ),
    "CURED/IN-VIO": (
        "Recorded as CURED/IN-VIO. The dataset does not define CURED. OATH and DOB guidance "
        "describe a cure as correcting the condition and filing a certificate of correction "
        "before the hearing, which admits the charge, with no penalty." + _NOT_A_DECISION
    ),
    "STIPULATION/IN-VIO": (
        "Recorded as STIPULATION/IN-VIO. The dataset does not define STIPULATION. In the "
        "project's OATH cross-check (33 summonses, 2026-10-23), OATH recorded each as "
        "STIPULATED, not IN VIOLATION. OATH and DOB guidance describe a stipulation as an "
        "agreement with DOB before the hearing that admits the violation, usually for a "
        "reduced penalty and time to correct." + _NOT_A_DECISION
    ),
    "ADMIT/IN-VIO": (
        "Recorded as ADMIT/IN-VIO. The dataset does not define ADMIT. OATH and DOB "
        "guidance describe admitting the violation and paying the penalty before the "
        "hearing." + _NOT_A_DECISION
    ),
    "POP/IN-VIO": (
        "Recorded as POP/IN-VIO. The meaning of POP is not documented, so how the IN-VIO "
        "outcome was reached is unknown." + _NOT_A_DECISION
    ),
    "DEFAULT": (
        "Recorded as DEFAULT. Its meaning is not documented in the ECB or OATH dataset "
        "dictionaries. OATH's "
        "rules and guidance say a party who misses the hearing is found in violation "
        "by default, with a higher penalty, and may ask OATH to vacate the default. The "
        "record does not say whether a default was later vacated. It is not a decision "
        "on the merits."
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
            "adjudication": ADJUDICATION_REVISION.get(r["hearing_status"], r["adjudication"]),
            "approved_2026_10_23": r["adjudication"],
            "note": ADJUDICATION_NOTES[r["hearing_status"]],
        }
        for r in mapping["ecb_adjudication"]
    }
    pairs = meas["infraction_pairs"]
    return {
        "generated_by": "backend/scripts/task5_build_ecb_reference.py",
        "sources": {
            "adjudication": "docs/task5/proposed_mappings.json (approved 2026-10-23 06:22Z)",
            "adjudication_revision": {
                "date": "2026-10-28",
                "reason": (
                    "Step 6 OATH cross-check: 33 summonses DOB records as STIPULATION/IN-VIO "
                    "read STIPULATED in OATH, and 2 ADMIT/IN-VIO have a blank OATH result. "
                    "Only IN VIOLATION stays grouped as upheld."
                ),
                "revised": ADJUDICATION_REVISION,
                "oath_sources": OATH_SOURCES,
            },
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
