"""Build the DOB complaints reference file used by the adapter (Task 5, step 2).

    uv run python scripts/task5_build_dob_reference.py

Reads the approved, committed sources and writes
src/nyc_apartment_agent/records/data/dob_complaints_reference.json:

- docs/task5/proposed_mappings.json: disposition codes, verbatim definitions, outcome,
  order event and order action (approved 2026-10-23 06:22Z);
- docs/task5/dob_complaint_categories_2018.json: DOB's complaint category list.

Nothing is typed by hand: a test rebuilds the file from these sources and compares.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "task5"
OUT = ROOT / "backend" / "src" / "nyc_apartment_agent" / "records" / "data"
OUT_FILE = OUT / "dob_complaints_reference.json"
# Outcomes that end a complaint (a status ACTIVE with one of them is a disagreement).
FINAL_OUTCOMES = (
    "no_violation",
    "referred",
    "unsubstantiated",
    "violation_issued",
    "violation_served",
)


def build() -> dict:
    mapping = json.loads((DOCS / "proposed_mappings.json").read_text(encoding="utf-8"))
    cats = json.loads((DOCS / "dob_complaint_categories_2018.json").read_text(encoding="utf-8"))
    pdf = mapping["sources"]["dob_pdf_2021"]
    dispositions = {
        r["code"]: {
            "definition": r["definition"],
            "definition_source": r["definition_source"],
            "outcome": r["outcome"],
            "order_event": r["order_event"],
            "order_action": r["order_action"],
        }
        for r in mapping["dob_disposition"]
    }
    return {
        "generated_by": "backend/scripts/task5_build_dob_reference.py",
        "sources": {
            "dispositions": {
                "file": "docs/task5/proposed_mappings.json",
                "pdf_url": pdf["source_url"],
                "pdf_sha256": pdf["sha256"],
                "pdf_revision": pdf["revision_marks"],
                "fallback": "6v9u-ndjg (2017) only where the 2021 PDF has no entry",
            },
            "categories": {
                "file": "docs/task5/dob_complaint_categories_2018.json",
                "pdf_url": cats["source_url"],
                "pdf_sha256": cats["sha256"],
                "pdf_revision": cats["revision_marks"],
            },
        },
        "final_outcomes": list(FINAL_OUTCOMES),
        "dispositions": dict(sorted(dispositions.items())),
        "categories": cats["codes"],
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(build(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT_FILE}")


if __name__ == "__main__":
    main()
