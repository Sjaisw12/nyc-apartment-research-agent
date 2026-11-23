"""Task 5 step 4: extract the 311 dictionary's complaint-detail tab into a committed file.

    uv run python scripts/task5_extract_311_dictionary.py PATH/TO/dictionary.xlsx

Source: "311_ServiceRequest_2020-present_DataDictionary_Updated_2025.xlsx", attached to
311 Service Requests from 2020 to Present (erm2-nwe9) on NYC Open Data:
https://data.cityofnewyork.us/api/views/erm2-nwe9/files/c5cc6926-45bb-4af3-8aab-0ee6a29bcf4d
Downloaded from that URL in the cloud environment on 2026-10-23; its SHA-256 is recorded.
The workbook itself is not committed.

Reads the "All Agencies Complaint<>Details" sheet with the standard library only (an
.xlsx file is a zip of XML) and writes docs/task5/sr311_dictionary_values.json: the
distinct values of Complaint Type, Descriptor, Additional_Details (descriptor_2) and
Location_Type, verbatim. "N/A" is the dictionary's marker for "not captured" and is not a value.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET  # noqa: S405 - a local file we downloaded

DOCS = Path(__file__).resolve().parents[2] / "docs" / "task5"
OUT = DOCS / "sr311_dictionary_values.json"
URL = "https://data.cityofnewyork.us/api/views/erm2-nwe9/files/c5cc6926-45bb-4af3-8aab-0ee6a29bcf4d"
SHEET = "All Agencies Complaint<>Details"
COLUMNS = {
    "Complaint Type": "complaint_type",
    "Descriptor": "descriptor",
    "Additional_Details": "descriptor_2",
    "Location_Type": "location_type",
}
M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _sheet_rows(path: Path, name: str) -> list[dict[str, str | None]]:
    z = zipfile.ZipFile(path)
    strings = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{M}si"):  # noqa: S314
            strings.append("".join(t.text or "" for t in si.iter(f"{M}t")))
    wb = ET.fromstring(z.read("xl/workbook.xml"))  # noqa: S314
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))  # noqa: S314
    targets = {r.get("Id"): r.get("Target") for r in rels}
    sheet = next(s for s in wb.find(f"{M}sheets") if s.get("name") == name)
    target = targets[sheet.get(f"{R}id")].lstrip("/").removeprefix("xl/")
    root = ET.fromstring(z.read(f"xl/{target}"))  # noqa: S314
    rows = []
    for r in root.iter(f"{M}row"):
        out: dict[str, str | None] = {}
        for c in r.findall(f"{M}c"):
            col = "".join(ch for ch in c.get("r") if ch.isalpha())
            v = c.find(f"{M}v")
            if c.get("t") == "s" and v is not None:
                out[col] = strings[int(v.text)]
            elif c.get("t") == "inlineStr":
                out[col] = "".join(x.text or "" for x in c.iter(f"{M}t"))
            else:
                out[col] = v.text if v is not None else None
        rows.append(out)
    return rows


def extract(path: Path) -> dict:
    rows = _sheet_rows(path, SHEET)
    header_at = next(
        i for i, r in enumerate(rows) if r.get("A") == "Agency" and r.get("B") == "Complaint Type"
    )
    header = rows[header_at]
    cols = {COLUMNS[v]: k for k, v in header.items() if v in COLUMNS}
    values: dict[str, set[str]] = {f: set() for f in COLUMNS.values()}
    data_rows = 0
    for r in rows[header_at + 1 :]:
        if not any(r.values()):
            continue
        data_rows += 1
        for field, col in cols.items():
            v = r.get(col)
            if v is not None and v.strip() and v.strip() != "N/A":
                values[field].add(v)
    return {
        "source_url": URL,
        "sheet": SHEET,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
        "downloaded": "2026-10-23",
        "rows": data_rows,
        "values": {f: sorted(v) for f, v in values.items()},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx", type=Path)
    out = extract(ap.parse_args().xlsx)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print({k: len(v) for k, v in out["values"].items()}, out["sha256"])


if __name__ == "__main__":
    main()
