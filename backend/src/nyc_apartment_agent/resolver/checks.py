"""Per-candidate checks: compare one PAD record from GeoSearch with the parsed input.

Nothing here uses GeoSearch's confidence or match_type.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..identifiers import BinStatus
from ..sources.geosearch import GeoSearchCandidate
from .models import BoroughMatch, NumberMatch, StreetMatch
from .normalize import (
    BOROUGH_CODES,
    ParsedAddress,
    edit_distance,
    normalize_house_number,
    normalize_street,
    numeric_tokens,
)

MAX_STREET_EDITS = 2
MIN_STREET_LEN_FOR_TYPO = 5


def compare_number(typed: str | None, pad: str | None) -> NumberMatch:
    a, b = normalize_house_number(typed), normalize_house_number(pad)
    if not a or not b:
        return NumberMatch.MISMATCH
    if a == b:
        return NumberMatch.EXACT
    if a.replace("-", "") == b.replace("-", ""):
        return NumberMatch.HYPHEN_ONLY
    return NumberMatch.MISMATCH


def compare_street(typed_norm: str | None, pad: str | None) -> StreetMatch:
    b = normalize_street(pad)
    if not typed_norm or not b:
        return StreetMatch.MISMATCH
    if typed_norm == b:
        return StreetMatch.EXACT
    # A typo is only "near" when every number in the street name agrees, so
    # "1 STREET" vs "2 STREET" (one edit apart) is a different street, not a typo.
    if (
        len(typed_norm) >= MIN_STREET_LEN_FOR_TYPO
        and numeric_tokens(typed_norm) == numeric_tokens(b)
        and edit_distance(typed_norm, b, MAX_STREET_EDITS) <= MAX_STREET_EDITS
    ):
        return StreetMatch.NEAR
    return StreetMatch.MISMATCH


def borough_consistent(c: GeoSearchCandidate) -> bool:
    """The candidate's own borough field must agree with its BBL's borough digit."""
    return bool(c.bbl and c.borough in BOROUGH_CODES and BOROUGH_CODES[c.borough] == c.bbl[0])


@dataclass
class Group:
    """Candidates that are certainly the same building (same BBL and same assigned BIN),
    or a single address record whose BIN is unresolved (never merged on BBL alone)."""

    key: tuple
    members: list[GeoSearchCandidate] = field(default_factory=list)
    found_by: set[str] = field(default_factory=set)

    @property
    def first(self) -> GeoSearchCandidate:
        return self.members[0]

    @property
    def labels(self) -> list[str]:
        seen: list[str] = []
        for m in self.members:
            if m.label not in seen:
                seen.append(m.label)
        return seen

    def best_number(self, parsed: ParsedAddress) -> NumberMatch:
        order = [NumberMatch.EXACT, NumberMatch.HYPHEN_ONLY, NumberMatch.MISMATCH]
        return min(
            (compare_number(parsed.house_number, m.housenumber) for m in self.members),
            key=order.index,
        )

    def best_street(self, parsed: ParsedAddress) -> StreetMatch:
        order = [StreetMatch.EXACT, StreetMatch.NEAR, StreetMatch.MISMATCH]
        return min(
            (compare_street(parsed.street_normalized, m.street) for m in self.members),
            key=order.index,
        )

    def best_member(self, parsed: ParsedAddress) -> GeoSearchCandidate:
        """The alias that matches the typed address best (for display)."""

        def score(m: GeoSearchCandidate) -> tuple[int, int]:
            n = compare_number(parsed.house_number, m.housenumber)
            s = compare_street(parsed.street_normalized, m.street)
            return (n is not NumberMatch.EXACT, s is not StreetMatch.EXACT)

        return min(self.members, key=score)


def group_key(c: GeoSearchCandidate) -> tuple:
    if c.bin_status is BinStatus.ASSIGNED:
        return ("building", c.bbl, c.bin)
    # Unresolved BIN: identity is the address record itself.
    return (
        "address",
        c.bbl,
        normalize_house_number(c.housenumber),
        normalize_street(c.street),
        c.bin_status.value,
    )


def build_groups(tagged: list[tuple[str, GeoSearchCandidate]]) -> list[Group]:
    groups: dict[tuple, Group] = {}
    for query_name, c in tagged:
        if not c.bbl or not borough_consistent(c):
            continue
        key = group_key(c)
        g = groups.setdefault(key, Group(key=key))
        if all(m.label != c.label for m in g.members):
            g.members.append(c)
        g.found_by.add(query_name)
    return list(groups.values())


def borough_match(
    group_borough: str | None, stated: str | None, inferred: str | None
) -> BoroughMatch:
    if stated:
        return (
            BoroughMatch.STATED_MATCH if group_borough == stated else BoroughMatch.STATED_CONFLICT
        )
    if inferred and group_borough == inferred:
        return BoroughMatch.INFERRED
    return BoroughMatch.UNKNOWN


_DIGITS_RE = re.compile(r"^\d{3,}$")


def queens_hyphenations(typed: str | None) -> list[str]:
    """Hyphenated forms a hyphenless Queens number could stand for ("7217" -> ["72-17"]).

    Queens grid numbers are "<cross street>-<two digits>", so only a two-digit right part
    is considered. Numbers shorter than 3 digits cannot be hyphenated this way.
    """
    if not typed or not _DIGITS_RE.match(typed):
        return []
    return [f"{typed[:-2]}-{typed[-2:]}"]


def queens_canonicalization_applies(group: Group, parsed: ParsedAddress) -> bool:
    """True when a Queens candidate matched only because PAD also lists the typed
    hyphenless number, so we must check whether an official hyphenated form exists."""
    first = group.first
    return (
        first.borough == "Queens"
        and bool(queens_hyphenations(parsed.house_number))
        and group.best_number(parsed) is NumberMatch.EXACT
        and group.best_street(parsed) is StreetMatch.EXACT
    )


def hyphenated_member(group: Group, parsed: ParsedAddress) -> str | None:
    for m in group.members:
        num = normalize_house_number(m.housenumber)
        if num and "-" in num and num.replace("-", "") == parsed.house_number:
            return num
    return None
