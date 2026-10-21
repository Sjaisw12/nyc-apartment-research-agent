"""Remove unit-level detail (apartment and floor) from source rows before they go public.

"Public" means: committed fixtures, logs, and the normalized records the API returns.
The unredacted row is kept only in the evidence store (see evidence.py).

What is redacted:
- the `apartment` and `story` fields (value replaced by a marker, so tests can still
  tell that a unit was named without seeing which one),
- in free text (`novdescription`): apartment ids ("APT 1H", "APTS 3A, 4B", "UNIT 2"),
  floors ("2nd STORY", "2 STY", "3RD FLOOR", "FL 4"), and position phrases
  ("1st APARTMENT FROM WEST AT NORTH").

Free-text redaction is pattern-based, so it is best-effort. It is idempotent, and the
fixture scan test re-runs it over every committed fixture to catch anything it missed.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

MARKER = "[REDACTED]"
UNIT_FIELDS = ("apartment", "story")
TEXT_FIELDS = ("novdescription",)

_DIR = r"(?:NORTH|SOUTH|EAST|WEST|FRONT|REAR)"
# A unit id: starts with a digit (1H, 12, 3-A), or is 1-2 letters with optional digits
# (H, PH, A12). Common short words are excluded so "APTS IN" or "APT AT" stay intact.
_STOP = r"(?!(?:IN|AT|OF|ON|TO|IS|BE|OR|AN|AS|BY|NO|IF|IT|SO|UP|DO|GO|ME|MY|WE)\b)"
_UNIT_ID = rf"(?:\[REDACTED\]|#?\d[A-Z0-9-]{{0,5}}|{_STOP}[A-Z]{{1,2}}\d{{0,3}})\b"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # "1st APARTMENT FROM WEST AT NORTH", "2nd APT FROM EAST"
    (
        re.compile(
            rf"\b\d{{1,2}}\s*(?:ST|ND|RD|TH)\s+(?:APARTMENT|APT)\.?\s+FROM\s+{_DIR}"
            rf"(?:\s+AT\s+{_DIR})?",
            re.IGNORECASE,
        ),
        "[UNIT POSITION REDACTED]",
    ),
    # "2nd STORY", "3RD FLOOR", "2 STY", "4 STORY", "FL 3", "FLOOR 3"
    (
        re.compile(
            r"\b\d{1,3}\s*(?:ST|ND|RD|TH)?\s*(?:STORY|STOREY|STY|FLOOR|FLR|FL)\b\.?",
            re.IGNORECASE,
        ),
        "[FLOOR REDACTED]",
    ),
    (
        re.compile(r"\b(?:FLOOR|FLR|FL)\.?\s*#?\d{1,3}\b", re.IGNORECASE),
        "[FLOOR REDACTED]",
    ),
    # "APT 1H", "APT. 12", "APTS 3A, 4B & 5C", "APARTMENT 2", "UNIT 4B", "APT NO. 3"
    (
        re.compile(
            rf"\b(APTS?|APARTMENTS?|UNITS?)(?:\.\s*|\s+|\s*(?=#))(?:NO\.?\s*|#\s*)?"
            rf"{_UNIT_ID}(?:\s*(?:,|&|AND)\s*{_UNIT_ID})*",
            re.IGNORECASE,
        ),
        r"\1 " + MARKER,
    ),
)


def redact_text(text: str | None) -> tuple[str | None, int]:
    """Return (redacted text, number of new replacements). Idempotent."""
    if not text:
        return text, 0
    total = 0
    for pattern, repl in _PATTERNS:

        def _sub(m: re.Match[str], repl: str = repl) -> str:
            nonlocal total
            new = m.expand(repl)
            if new != m.group(0):  # re-matching an existing marker is not a new redaction
                total += 1
            return new

        text = pattern.sub(_sub, text)
    return text, total


def needs_redaction(text: str | None) -> bool:
    """True if the text still contains unit/floor detail (used by the fixture scan)."""
    return redact_text(text)[1] > 0


def redact_row(row: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Copy of a source row with unit fields masked. Returns (row, redacted field names)."""
    out = dict(row)
    changed: list[str] = []
    for f in UNIT_FIELDS:
        if f in out and out[f] not in (None, "", MARKER):
            out[f] = MARKER
            changed.append(f)
    for f in TEXT_FIELDS:
        if f in out:
            new, n = redact_text(out[f])
            if n and new != out[f]:
                out[f] = new
                changed.append(f)
    return out, changed


def canonical_sha256(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
