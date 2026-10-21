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
import hmac
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

_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    # "1st APARTMENT FROM WEST AT NORTH", "2nd APT FROM EAST"
    (
        "unit_position",
        re.compile(
            rf"\b\d{{1,2}}\s*(?:ST|ND|RD|TH)\s+(?:APARTMENT|APT)\.?\s+FROM\s+{_DIR}"
            rf"(?:\s+AT\s+{_DIR})?",
            re.IGNORECASE,
        ),
        "[UNIT POSITION REDACTED]",
    ),
    # "2nd STORY", "3RD FLOOR", "2 STY", "4 STORY", "FL 3", "FLOOR 3"
    (
        "floor",
        re.compile(
            r"\b\d{1,3}\s*(?:ST|ND|RD|TH)?\s*(?:STORY|STOREY|STY|FLOOR|FLR|FL)\b\.?",
            re.IGNORECASE,
        ),
        "[FLOOR REDACTED]",
    ),
    (
        "floor",
        re.compile(r"\b(?:FLOOR|FLR|FL)\.?\s*#?\d{1,3}\b", re.IGNORECASE),
        "[FLOOR REDACTED]",
    ),
    # "APT 1H", "APT. 12", "APTS 3A, 4B & 5C", "APARTMENT 2", "UNIT 4B", "APT NO. 3"
    (
        "unit_id",
        re.compile(
            rf"\b(APTS?|APARTMENTS?|UNITS?)(?:\.\s*|\s+|\s*(?=#))(?:NO\.?\s*|#\s*)?"
            rf"{_UNIT_ID}(?:\s*(?:,|&|AND)\s*{_UNIT_ID})*",
            re.IGNORECASE,
        ),
        r"\1 " + MARKER,
    ),
)


REDACTION_KINDS = ("unit_id", "floor", "unit_position")

# Markers left in sanitized text, one per replacement, so redaction events can be
# counted again from a sanitized fixture without ever seeing the original text.
_MARKER_PATTERNS = {
    "unit_position": re.compile(r"\[UNIT POSITION REDACTED\]"),
    "floor": re.compile(r"\[FLOOR REDACTED\]"),
    "unit_id": re.compile(r"\b(?:APTS?|APARTMENTS?|UNITS?) \[REDACTED\]", re.IGNORECASE),
}


def redact_text_detailed(text: str | None) -> tuple[str | None, dict[str, int]]:
    """Return (redacted text, new replacements per kind). Idempotent."""
    counts: dict[str, int] = {}
    if not text:
        return text, counts
    for kind, pattern, repl in _PATTERNS:

        def _sub(m: re.Match[str], repl: str = repl, kind: str = kind) -> str:
            new = m.expand(repl)
            if new != m.group(0):  # re-matching an existing marker is not a new redaction
                counts[kind] = counts.get(kind, 0) + 1
            return new

        text = pattern.sub(_sub, text)
    return text, counts


def redact_text(text: str | None) -> tuple[str | None, int]:
    """Return (redacted text, number of new replacements). Idempotent."""
    text, counts = redact_text_detailed(text)
    return text, sum(counts.values())


def needs_redaction(text: str | None) -> bool:
    """True if the text still contains unit/floor detail (used by the fixture scan)."""
    return redact_text(text)[1] > 0


def count_markers(text: str | None) -> dict[str, int]:
    """Replacements per kind already present in sanitized text."""
    if not text:
        return {}
    out = {k: len(p.findall(text)) for k, p in _MARKER_PATTERNS.items()}
    return {k: v for k, v in out.items() if v}


def redact_row_event(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Redact a row; also return what was removed *in this call* (never the values)."""
    out = dict(row)
    masked: list[str] = []
    for f in UNIT_FIELDS:
        if f in out and out[f] not in (None, "", MARKER):
            out[f] = MARKER
            masked.append(f)
    replacements: dict[str, int] = {}
    text_fields: list[str] = []
    for f in TEXT_FIELDS:
        if f in out:
            new, counts = redact_text_detailed(out[f])
            if counts:
                out[f] = new
                text_fields.append(f)
                for k, v in counts.items():
                    replacements[k] = replacements.get(k, 0) + v
    return out, {
        "fields_masked": masked,
        "text_fields_redacted": text_fields,
        "text_replacements": replacements,
    }


def redact_row(row: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Copy of a source row with unit fields masked. Returns (row, redacted field names)."""
    out, event = redact_row_event(row)
    return out, event["fields_masked"] + event["text_fields_redacted"]


def event_from_sanitized(row: dict[str, Any]) -> dict[str, Any]:
    """Rebuild a row's redaction event from its markers (for already-sanitized rows)."""
    replacements: dict[str, int] = {}
    text_fields: list[str] = []
    for f in TEXT_FIELDS:
        counts = count_markers(row.get(f))
        if counts:
            text_fields.append(f)
            for k, v in counts.items():
                replacements[k] = replacements.get(k, 0) + v
    return {
        "fields_masked": [f for f in UNIT_FIELDS if row.get(f) == MARKER],
        "text_fields_redacted": text_fields,
        "text_replacements": replacements,
    }


def event_is_empty(event: dict[str, Any]) -> bool:
    return not (event["fields_masked"] or event["text_fields_redacted"])


def canonical_sha256(obj: Any) -> str:
    """Plain SHA-256. Safe for public (redacted) rows only; see keyed_digest."""
    return hashlib.sha256(canonical_json(obj)).hexdigest()


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def keyed_digest(obj: Any, key: bytes) -> str:
    """HMAC-SHA256 of a row under a server-side key.

    Used for the *original* (unredacted) row in public records. A plain SHA-256 of the
    original row would leak the unit: the redacted row is public, the hidden parts
    (apartment, floor, position) have only thousands of combinations, so they could be
    recovered by trying each and comparing hashes. Without the key that is not possible.
    """
    return hmac.new(key, canonical_json(obj), hashlib.sha256).hexdigest()
