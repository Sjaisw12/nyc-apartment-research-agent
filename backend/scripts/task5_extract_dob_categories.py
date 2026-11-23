"""Task 5: extract DOB's complaint category list from the PDF attached to eabe-havv.

    uv run python scripts/task5_extract_dob_categories.py PATH/TO/categories.pdf

Source: the "Complaint Category Description" PDF (NYC Department of Buildings,
"Rev. 12/18") attached to the DOB Complaints Received dataset on NYC Open Data:
https://data.cityofnewyork.us/api/views/eabe-havv/files/dc709ed2-7af1-429c-92c9-71ec3a4c23fa
It was downloaded from that URL in the cloud environment on 2026-10-23; its SHA-256 is
recorded. The PDF itself is not committed.

Requires `pdftotext` (poppler). Writes docs/task5/dob_complaint_categories_2018.json
with every code, its description verbatim (only line wrapping is joined) and DOB's
priority letter. Counts of codes in use come from the recorded step-1 measurements.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs" / "task5"
OUT = DOCS / "dob_complaint_categories_2018.json"
URL = "https://data.cityofnewyork.us/api/views/eabe-havv/files/dc709ed2-7af1-429c-92c9-71ec3a4c23fa"
CODE_LINE = re.compile(r"^\s{1,8}([0-9A-Z]{2})(?:\s{2,}(\S.*?))?\s{2,}([A-D])\s*$")
CONTINUATION = re.compile(r"^\s{10,}(\S.*?)\s*$")
SKIP = re.compile(r"(nyc\.gov|Rev\. |\d of \d|Complaint Category Description|build safe)")


def _run(tool: str, *args: str) -> str:
    exe = shutil.which(tool)
    if exe is None:
        raise SystemExit(f"{tool} (poppler) is required")
    # Fixed tool, local file path argument; no shell.
    return subprocess.run([exe, *args], capture_output=True, text=True, check=True).stdout  # noqa: S603


def extract(text: str) -> dict[str, dict[str, str]]:
    """Code lines carry the code and the priority. A long description can wrap onto lines
    above and below a code line that holds no description (code 23 in Rev. 12/18)."""
    codes: dict[str, dict[str, str]] = {}
    before: list[str] = []  # continuation lines seen since the last code line
    open_code: str | None = None  # code line without a description, still collecting
    for line in text.splitlines():
        if not line.strip() or SKIP.search(line) or line.strip().startswith("CODE"):
            if open_code:
                open_code = None
            before = []
            continue
        m = CODE_LINE.match(line)
        if m:
            code, desc, priority = m.groups()
            if code in codes:
                raise ValueError(f"code {code} appears twice")
            if desc:
                codes[code] = {"description": desc, "priority": priority}
                open_code = None
            else:
                codes[code] = {"description": " ".join(before), "priority": priority}
                open_code = code
            before = []
            continue
        c = CONTINUATION.match(line)
        if c:
            if open_code:
                codes[open_code]["description"] += " " + c.group(1)
            else:
                before.append(c.group(1))
            continue
        raise ValueError(f"unparsed line: {line!r}")
    if before:
        raise ValueError(f"text with no code: {before!r}")
    empty = [c for c, v in codes.items() if not v["description"].strip()]
    if empty:
        raise ValueError(f"codes without a description: {empty}")
    return codes


def pdf_meta(path: Path) -> dict[str, str]:
    keep = ("Title", "Author", "CreationDate", "ModDate", "Pages")
    out = {}
    for line in _run("pdfinfo", str(path)).splitlines():
        k, _, v = line.partition(":")
        if k in keep:
            out[k] = v.strip()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    raw = args.pdf.read_bytes()
    text = _run("pdftotext", "-layout", str(args.pdf), "-")
    codes = extract(text)
    measured = json.loads((DOCS / "step1_measurements.json").read_text(encoding="utf-8"))
    in_use: dict[str, int] = measured["results"]["dob_complaint_category"]
    not_listed = {c: n for c, n in sorted(in_use.items()) if c not in codes}
    payload = {
        "source_url": URL,
        "source_title": "Complaint Category Description (NYC Department of Buildings)",
        "obtained": "downloaded from source_url (NYC Open Data, file attached to dataset "
        "eabe-havv) in the cloud environment on 2026-10-23",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "pdf_metadata": pdf_meta(args.pdf),
        "revision_marks": sorted(set(re.findall(r"Rev\. ?(\d{1,2}/\d{2})", text))),
        "code_count": len(codes),
        "codes": dict(sorted(codes.items())),
        "coverage": {
            "counts": "docs/task5/step1_measurements.json (2026-10-23 04:39Z); no new snapshot",
            "codes_in_use": len(in_use),
            "rows": sum(in_use.values()),
            "codes_in_use_not_listed": len(not_listed),
            "rows_with_codes_not_listed": sum(not_listed.values()),
            "not_listed": not_listed,
        },
    }
    args.out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(codes)} codes -> {args.out}")
    print(json.dumps({k: v for k, v in payload["coverage"].items() if k != "not_listed"}))


if __name__ == "__main__":
    main()
