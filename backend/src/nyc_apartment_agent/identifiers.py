"""Validation for NYC property identifiers.

BBL (Borough-Block-Lot): 10 digits = borough (1) + block (5) + lot (4). Identifies a tax lot.
BIN (Building Identification Number): 7 digits = borough (1) + 6-digit serial.
    Identifies a building. One BBL can hold several BINs.
Borough codes: 1 Manhattan, 2 Bronx, 3 Brooklyn, 4 Queens, 5 Staten Island.

We validate identifiers before putting them into any query, so user-supplied text can never
change the meaning of a SoQL filter.
"""

from __future__ import annotations

import re
from enum import StrEnum

from .errors import InvalidIdentifierError

BOROUGHS = {
    "1": "Manhattan",
    "2": "Bronx",
    "3": "Brooklyn",
    "4": "Queens",
    "5": "Staten Island",
}

_BBL_RE = re.compile(r"^[1-5]\d{5}\d{4}$")
_BIN_RE = re.compile(r"^[1-5]\d{6}$")


class BinStatus(StrEnum):
    ASSIGNED = "assigned"
    # Borough digit followed by six zeros (1000000 ... 5000000). Official NYC data
    # dictionaries describe these as "unassigned or unknown"; see
    # docs/bin_2000000_investigation.md. Such a BIN is shared by many unrelated lots,
    # so it must never be used as a join key.
    UNASSIGNED_PLACEHOLDER = "unassigned_placeholder"
    MISSING = "missing"


def normalize_bbl(value: str | int) -> str:
    """Return a validated 10-digit BBL string or raise InvalidIdentifierError."""
    text = str(value).strip()
    if not _BBL_RE.match(text):
        raise InvalidIdentifierError(f"not a valid 10-digit BBL: {value!r}")
    if text[1:6] == "00000":
        raise InvalidIdentifierError(f"BBL has block 0: {value!r}")
    return text


def normalize_bin(value: str | int) -> str:
    """Return a validated 7-digit BIN string or raise InvalidIdentifierError."""
    text = str(value).strip()
    if not _BIN_RE.match(text):
        raise InvalidIdentifierError(f"not a valid 7-digit BIN: {value!r}")
    return text


def classify_bin(value: str | int | None) -> BinStatus:
    """Classify a BIN as assigned, a borough placeholder, or missing.

    Raises InvalidIdentifierError for values that are not BIN-shaped at all.
    """
    if value is None or str(value).strip() == "":
        return BinStatus.MISSING
    text = normalize_bin(value)
    if text[1:] == "000000":
        return BinStatus.UNASSIGNED_PLACEHOLDER
    return BinStatus.ASSIGNED


def borough_of(identifier: str) -> str:
    """Borough name from the first digit of a BBL or BIN."""
    return BOROUGHS[identifier[0]]
