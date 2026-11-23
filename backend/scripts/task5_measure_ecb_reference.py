"""Task 5 step 3: measure the ECB reference lists the adapter needs (read-only aggregates).

    uv run python scripts/task5_measure_ecb_reference.py

Writes docs/task5/ecb_reference_measurements.json with, for DOB ECB Violations
(6bgk-3dad), every query, its time and its X-SODA2-Truth-Last-Modified header:

- every (infraction_codeN, section_law_descriptionN) pair, N = 1..10, with counts;
- the `aggravated_level` vocabulary.

Design 18.2: a section/law text is shown only when its code and text match this measured
list. The pairs are a fixed code list (law sections and DOB's short text), not free text,
and nothing personal is fetched: only grouped counts.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from nyc_apartment_agent.records.redaction import needs_redaction
from nyc_apartment_agent.sources.clients import socrata_client

OUT = Path(__file__).resolve().parents[2] / "docs" / "task5" / "ecb_reference_measurements.json"
DATASET = "6bgk-3dad"
_PRIVATE = re.compile(r"@|\b\d{3}[-. )]+\d{3}[-. ]\d{4}\b|\b(?:MR|MRS|MS|MISS|DR)\.?\s+[A-Z]")


def main() -> None:
    log: list[dict] = []
    pairs: dict[tuple[str, str], int] = defaultdict(int)
    by_field: dict[str, int] = {}
    with socrata_client() as soc:

        def run(params: dict) -> list[dict]:
            resp = soc.get_json(f"/resource/{DATASET}.json", params=params)
            log.append(
                {
                    "params": params,
                    "fetched_at": resp.fetched_at,
                    "truth_header": resp.headers.get("x-soda2-truth-last-modified"),
                    "rows": len(resp.data),
                }
            )
            return resp.data

        for n in range(1, 11):
            rows = run(
                {
                    "$select": f"infraction_code{n} AS code, section_law_description{n} AS text, "
                    "count(*) AS n",
                    "$where": f"infraction_code{n} IS NOT NULL",
                    "$group": f"infraction_code{n}, section_law_description{n}",
                    "$limit": 50000,
                }
            )
            by_field[f"infraction_code{n}"] = len(rows)
            for r in rows:
                pairs[(r.get("code"), r.get("text"))] += int(r["n"])
        aggravated = run(
            {
                "$select": "aggravated_level, count(*) AS n",
                "$group": "aggravated_level",
                "$limit": 1000,
            }
        )

    texts_by_code: dict[str, set] = defaultdict(set)
    for code, text in pairs:
        texts_by_code[code].add(text)
    entries = []
    for (code, text), n in sorted(pairs.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        reasons = []
        if code is None or text is None:
            reasons.append("code_or_text_missing")
        if len(texts_by_code[code]) > 1:
            reasons.append("code_has_several_texts")
        if text and (needs_redaction(text) or _PRIVATE.search(text)):
            reasons.append("privacy_pattern")
        entries.append(
            {"code": code, "text": text, "rows": n, "allowlisted": not reasons, "reasons": reasons}
        )
    headers = {e["truth_header"] for e in log}
    payload = {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset": DATASET,
        "truth_headers": sorted(h for h in headers if h),
        "single_version": len(headers) == 1,
        "rule": "a (code, text) pair is allowlisted when both are present, the code has "
        "exactly one text across all ten columns citywide, and the text has no unit, "
        "floor, email, phone or honorific pattern",
        "pairs": len(entries),
        "allowlisted": sum(e["allowlisted"] for e in entries),
        "distinct_codes": len(texts_by_code),
        "pairs_per_field": by_field,
        "infraction_pairs": entries,
        "aggravated_level": {str(r.get("aggravated_level")): int(r["n"]) for r in aggravated},
        "query_log": log,
    }
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    keys = ("pairs", "allowlisted", "distinct_codes", "truth_headers")
    print(json.dumps({k: payload[k] for k in keys}))


if __name__ == "__main__":
    main()
