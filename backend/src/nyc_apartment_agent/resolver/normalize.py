"""Parse and normalize user-typed NYC addresses. Pure functions, no network.

The same `normalize_street` is applied to the user's text and to every GeoSearch candidate,
so both sides are compared in one canonical form (e.g. "W 42nd St" == "WEST 42 STREET").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

BOROUGH_CODES = {
    "Manhattan": "1",
    "Bronx": "2",
    "Brooklyn": "3",
    "Queens": "4",
    "Staten Island": "5",
}
CODE_TO_BOROUGH = {v: k for k, v in BOROUGH_CODES.items()}

# Words a user may write for a borough. "New York" is the postal city for Manhattan.
_BOROUGH_ALIASES = {
    "MANHATTAN": "Manhattan",
    "NEW YORK": "Manhattan",
    "NEW YORK CITY": None,  # the whole city: says nothing about the borough
    "NYC": None,
    "BRONX": "Bronx",
    "THE BRONX": "Bronx",
    "BROOKLYN": "Brooklyn",
    "KINGS": "Brooklyn",
    "KINGS COUNTY": "Brooklyn",
    "QUEENS": "Queens",
    "QUEENS COUNTY": "Queens",
    "STATEN ISLAND": "Staten Island",
    "RICHMOND": "Staten Island",
    "RICHMOND COUNTY": "Staten Island",
    "BRONX COUNTY": "Bronx",
    "NEW YORK COUNTY": "Manhattan",
}

_US_STATES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "DC",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
}

_SUFFIXES = {
    "ST": "STREET",
    "STR": "STREET",
    "AVE": "AVENUE",
    "AV": "AVENUE",
    "AVENU": "AVENUE",
    "RD": "ROAD",
    "PL": "PLACE",
    "BLVD": "BOULEVARD",
    "DR": "DRIVE",
    "TER": "TERRACE",
    "TERR": "TERRACE",
    "PKWY": "PARKWAY",
    "LN": "LANE",
    "CT": "COURT",
    "SQ": "SQUARE",
    "HWY": "HIGHWAY",
    "EXPY": "EXPRESSWAY",
    "TPKE": "TURNPIKE",
    "CIR": "CIRCLE",
    "PLZ": "PLAZA",
    "BWAY": "BROADWAY",
    "B'WAY": "BROADWAY",
}
_DIRECTIONS = {"E": "EAST", "W": "WEST", "N": "NORTH", "S": "SOUTH"}
_ORDINAL_WORDS = {
    "FIRST": "1",
    "SECOND": "2",
    "THIRD": "3",
    "FOURTH": "4",
    "FIFTH": "5",
    "SIXTH": "6",
    "SEVENTH": "7",
    "EIGHTH": "8",
    "NINTH": "9",
    "TENTH": "10",
    "ELEVENTH": "11",
    "TWELFTH": "12",
}
_ORDINAL_RE = re.compile(r"^(\d+)(ST|ND|RD|TH)$")

# Spelled-out street numbers ("West Seventeenth Street", "One Hundred Twenty-Fifth St").
_UNIT_CARDINAL = {
    "ONE": 1,
    "TWO": 2,
    "THREE": 3,
    "FOUR": 4,
    "FIVE": 5,
    "SIX": 6,
    "SEVEN": 7,
    "EIGHT": 8,
    "NINE": 9,
}
_TEEN_CARDINAL = {
    "TEN": 10,
    "ELEVEN": 11,
    "TWELVE": 12,
    "THIRTEEN": 13,
    "FOURTEEN": 14,
    "FIFTEEN": 15,
    "SIXTEEN": 16,
    "SEVENTEEN": 17,
    "EIGHTEEN": 18,
    "NINETEEN": 19,
}
_TENS_CARDINAL = {
    "TWENTY": 20,
    "THIRTY": 30,
    "FORTY": 40,
    "FIFTY": 50,
    "SIXTY": 60,
    "SEVENTY": 70,
    "EIGHTY": 80,
    "NINETY": 90,
}
_UNIT_ORDINAL = {
    "FIRST": 1,
    "SECOND": 2,
    "THIRD": 3,
    "FOURTH": 4,
    "FIFTH": 5,
    "SIXTH": 6,
    "SEVENTH": 7,
    "EIGHTH": 8,
    "NINTH": 9,
}
_TEEN_ORDINAL = {
    "TENTH": 10,
    "ELEVENTH": 11,
    "TWELFTH": 12,
    "THIRTEENTH": 13,
    "FOURTEENTH": 14,
    "FIFTEENTH": 15,
    "SIXTEENTH": 16,
    "SEVENTEENTH": 17,
    "EIGHTEENTH": 18,
    "NINETEENTH": 19,
}
_TENS_ORDINAL = {
    "TWENTIETH": 20,
    "THIRTIETH": 30,
    "FORTIETH": 40,
    "FIFTIETH": 50,
    "SIXTIETH": 60,
    "SEVENTIETH": 70,
    "EIGHTIETH": 80,
    "NINETIETH": 90,
}
_NUMBER_WORDS = (
    set(_UNIT_CARDINAL)
    | set(_TEEN_CARDINAL)
    | set(_TENS_CARDINAL)
    | set(_UNIT_ORDINAL)
    | set(_TEEN_ORDINAL)
    | set(_TENS_ORDINAL)
    | {"HUNDRED", "HUNDREDTH", "AND"}
)


def _ordinal_suffix(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "TH"
    return {1: "ST", 2: "ND", 3: "RD"}.get(n % 10, "TH")


def _parse_spelled_ordinal(words: list[str]) -> int | None:
    """Value of a spelled ordinal such as ["ONE", "HUNDRED", "TWENTY", "FIFTH"] (1-199).

    Returns None unless the words form one well-formed ordinal number, so ordinary
    street names ("FIVE TOWNS", "SEVEN OAKS") are never changed.
    """
    i, total = 0, 0
    if len(words) >= 2 and words[0] in ("ONE", "A") and words[1] in ("HUNDRED", "HUNDREDTH"):
        if words[1] == "HUNDREDTH":
            return 100 if len(words) == 2 else None
        total, i = 100, 2
        if i < len(words) and words[i] == "AND":
            i += 1
    rest = words[i:]
    if len(rest) == 1:
        w = rest[0]
        for table in (_UNIT_ORDINAL, _TEEN_ORDINAL, _TENS_ORDINAL):
            if w in table:
                return total + table[w]
        return None
    if len(rest) == 2 and rest[0] in _TENS_CARDINAL and rest[1] in _UNIT_ORDINAL:
        return total + _TENS_CARDINAL[rest[0]] + _UNIT_ORDINAL[rest[1]]
    return None


def numeric_ordinals(text: str) -> str:
    """Rewrite spelled-out ordinal street numbers as numerals with a suffix.

    "220 West Seventeenth Street" -> "220 West 17TH Street";
    "Twenty-First Ave" -> "21ST Ave". Everything else is left exactly as typed.
    """
    if not text:
        return text
    words = text.replace("-", " - ").split()
    out: list[str] = []
    i = 0
    while i < len(words):
        best = None
        # Longest run of number words (hyphens allowed between them) that parses.
        j, run = i, []
        while j < len(words) and (words[j].upper() in _NUMBER_WORDS or (words[j] == "-" and run)):
            if words[j] != "-":
                run.append(words[j].upper())
                n = _parse_spelled_ordinal(run)
                if n is not None:
                    best = (j + 1, n)
            j += 1
        if best:
            end, n = best
            out.append(f"{n}{_ordinal_suffix(n)}")
            i = end
        else:
            out.append(words[i])
            i += 1
    return re.sub(r"\s*-\s*", "-", " ".join(out))


_HOUSE_RE = re.compile(r"^\s*(\d+[A-Z]?(?:-\d+[A-Z]?)?)\s+(.+)$", re.IGNORECASE)
_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")

MAX_INPUT_CHARS = 200


@dataclass
class ParsedAddress:
    raw: str
    house_number: str | None = None
    street: str | None = None  # as typed (trimmed)
    street_normalized: str | None = None
    borough: str | None = None  # only when the user stated it
    zip_code: str | None = None
    state: str | None = None
    notes: list[str] = field(default_factory=list)


def normalize_house_number(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", "", value.upper())
    return text or None


def normalize_street(value: str | None) -> str | None:
    """Canonical street form: upper case, expanded suffixes/directions, numeric ordinals."""
    if not value:
        return None
    text = numeric_ordinals(value).upper().replace(".", " ").replace(",", " ")
    tokens = [t for t in re.split(r"\s+", text) if t]
    out: list[str] = []
    for i, tok in enumerate(tokens):
        if tok in _SUFFIXES:
            # Only expand suffix abbreviations after the first word ("ST NICHOLAS" stays).
            tok = _SUFFIXES[tok] if (i > 0 or tok in ("BWAY", "B'WAY")) else tok
        if tok in _DIRECTIONS and i == 0 and len(tokens) > 1:
            tok = _DIRECTIONS[tok]
        if tok in _ORDINAL_WORDS:
            tok = _ORDINAL_WORDS[tok]
        m = _ORDINAL_RE.match(tok)
        if m:
            tok = m.group(1)
        out.append(tok.replace("'", ""))
    return " ".join(out) or None


def borough_from_text(value: str) -> str | None:
    return _BOROUGH_ALIASES.get(re.sub(r"\s+", " ", value.strip().upper().replace(".", "")))


def parse_address(raw: str) -> ParsedAddress:
    """Split typed text into house number, street, borough, ZIP and state.

    The parser is deliberately conservative: anything it cannot recognize is left out
    (and recorded in `notes`) rather than guessed.
    """
    parsed = ParsedAddress(raw=raw)
    text = (raw or "").strip()
    if not text:
        return parsed

    parts = [p.strip() for p in text.split(",") if p.strip()]
    first, rest = parts[0], parts[1:]

    # A ZIP can only appear after the street part, never as the house number.
    for seg in rest:
        zip_match = _ZIP_RE.search(seg)
        if zip_match:
            parsed.zip_code = zip_match.group(1)
    if not rest:
        tail = re.search(r"\s(\d{5})\s*$", first)
        if tail:
            parsed.zip_code = tail.group(1)
            first = first[: tail.start()].strip()

    # Trailing segments: borough / city / "NY 10001" / country.
    for seg in rest:
        seg_clean = _ZIP_RE.sub("", seg).strip()
        seg_upper = seg_clean.upper()
        if seg_upper in ("USA", "US", "UNITED STATES", ""):
            continue
        if seg_upper in _US_STATES:
            parsed.state = seg_upper
            continue
        borough = borough_from_text(seg_clean)
        if borough is not None:
            parsed.borough = parsed.borough or borough
            continue
        if seg_upper in _BOROUGH_ALIASES:  # "NYC": known but not a borough
            continue
        parsed.notes.append(f"unrecognized place name: {seg_clean!r}")

    # No commas: allow a trailing borough name, e.g. "1134 West Farms Rd Bronx".
    if not rest:
        for alias in sorted(_BOROUGH_ALIASES, key=len, reverse=True):
            if first.upper().endswith(" " + alias) and _BOROUGH_ALIASES[alias]:
                parsed.borough = _BOROUGH_ALIASES[alias]
                first = first[: -len(alias)].strip()
                break

    m = _HOUSE_RE.match(first)
    if m:
        parsed.house_number = normalize_house_number(m.group(1))
        parsed.street = m.group(2).strip()
    else:
        parsed.street = first
        parsed.notes.append("no house number found")
    parsed.street_normalized = normalize_street(parsed.street)
    return parsed


def edit_distance(a: str, b: str, limit: int = 3) -> int:
    """Levenshtein distance, stopping early once it exceeds `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def numeric_tokens(street: str) -> list[str]:
    return re.findall(r"\d+", street)
