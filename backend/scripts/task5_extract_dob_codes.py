"""Task 5: extract DOB's BIS complaint disposition codes from the official PDF.

    uv run python scripts/task5_extract_dob_codes.py PATH/TO/bis_complaint_disposition_codes.pdf

Source: https://www.nyc.gov/assets/buildings/pdf/bis_complaint_disposition_codes.pdf
("BIS Complaint Disposition Codes", NYC Department of Buildings, "Rev. 09/21"). nyc.gov
is not reachable from the cloud environment, so the copy used was downloaded by Sanyam
from that URL and supplied through the project on 2026-10-23. Its SHA-256 is recorded.

Requires `pdftotext` (poppler). Writes docs/task5/dob_disposition_codes_2021.json with
every code and its disposition text verbatim (only line wrapping is joined). The PDF
itself is not committed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "docs" / "task5" / "dob_disposition_codes_2021.json"
URL = "https://www.nyc.gov/assets/buildings/pdf/bis_complaint_disposition_codes.pdf"
CODE_LINE = re.compile(r"^\s{1,8}([A-Z0-9]{2})\s{2,}(\S.*?)\s*$")
CONTINUATION = re.compile(r"^\s{10,}(\S.*?)\s*$")
SKIP = re.compile(
    r"(nyc\.gov|Rev\. |page \d of|DISPOSITION CODES|Disposition Codes|BIS Complaint"
    r"|CODE\s+DISPOSITION TEXT|build safe)"
)


def extract(text: str) -> dict[str, str]:
    codes: dict[str, str] = {}
    last: str | None = None
    for line in text.splitlines():
        if not line.strip():
            last = None
            continue
        # A code line is matched first: some disposition texts contain words that also
        # appear in the page headers (H2 mentions "Pre-BIS Complaint Number").
        m = CODE_LINE.match(line)
        if m:
            code, desc = m.groups()
            if code in codes:
                raise ValueError(f"code {code} appears twice")
            codes[code] = desc
            last = code
            continue
        if SKIP.search(line):
            continue
        c = CONTINUATION.match(line)
        if c and last:
            codes[last] = f"{codes[last]} {c.group(1)}"
            continue
        raise ValueError(f"unparsed line: {line!r}")
    return codes


def _run(tool: str, *args: str) -> str:
    exe = shutil.which(tool)
    if exe is None:
        raise SystemExit(f"{tool} (poppler) is required")
    # Fixed tool, local file path argument; no shell.
    return subprocess.run([exe, *args], capture_output=True, text=True, check=True).stdout  # noqa: S603


def pdf_meta(path: Path) -> dict[str, str]:
    info = _run("pdfinfo", str(path))
    keep = ("Title", "Author", "CreationDate", "ModDate", "Pages")
    out = {}
    for line in info.splitlines():
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
    revisions = sorted(set(re.findall(r"Rev\. ?(\d{1,2}/\d{2})", text)))
    payload = {
        "source_url": URL,
        "obtained": "copy downloaded from source_url by Sanyam and supplied through the "
        "project on 2026-10-23 (nyc.gov is blocked from the cloud environment)",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "pdf_metadata": pdf_meta(args.pdf),
        "revision_marks": revisions,
        "code_count": len(codes),
        "codes": dict(sorted(codes.items())),
    }
    args.out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(codes)} codes -> {args.out}")


if __name__ == "__main__":
    main()
