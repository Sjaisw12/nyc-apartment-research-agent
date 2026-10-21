from nyc_apartment_agent.identifiers import BinStatus
from nyc_apartment_agent.resolver.checks import (
    build_groups,
    compare_number,
    compare_street,
    queens_hyphenations,
)
from nyc_apartment_agent.resolver.models import NumberMatch, StreetMatch
from nyc_apartment_agent.sources.geosearch import GeoSearchCandidate


def cand(label, number, street, bbl, bin_value, borough="Bronx"):
    status = (
        BinStatus.MISSING
        if bin_value is None
        else BinStatus.UNASSIGNED_PLACEHOLDER
        if bin_value.endswith("000000")
        else BinStatus.ASSIGNED
    )
    return GeoSearchCandidate(
        label=label,
        housenumber=number,
        street=street,
        borough=borough,
        postalcode="10459",
        bbl=bbl,
        bin=bin_value,
        bin_status=status,
        pad_version="26c",
        match_type="fallback",
        confidence=0.8,
        longitude=-73.9,
        latitude=40.8,
    )


def test_house_numbers():
    assert compare_number("1134", "1134") is NumberMatch.EXACT
    assert compare_number("3343", "33-43") is NumberMatch.HYPHEN_ONLY
    assert compare_number("1A", "1") is NumberMatch.MISMATCH
    assert compare_number("873", "873 GARAGE") is NumberMatch.MISMATCH
    assert compare_number("99999", "98") is NumberMatch.MISMATCH
    assert compare_number("1", None) is NumberMatch.MISMATCH


def test_streets():
    assert compare_street("WEST FARMS ROAD", "WEST FARMS ROAD") is StreetMatch.EXACT
    assert compare_street("1 AVENUE", "FIRST AVENUE") is StreetMatch.EXACT
    assert compare_street("2 STRET", "2 STREET") is StreetMatch.NEAR
    # One character apart, but different numbered streets: never a typo.
    assert compare_street("1 STREET", "2 STREET") is StreetMatch.MISMATCH
    assert compare_street("EAST 2 STREET", "2 STREET") is StreetMatch.MISMATCH


def test_same_bbl_and_assigned_bin_merge_as_aliases():
    groups = build_groups(
        [
            (
                "main",
                cand("1 CENTER STREET", "1", "CENTER STREET", "1001210001", "1001394", "Manhattan"),
            ),
            (
                "main",
                cand("1 CENTRE STREET", "1", "CENTRE STREET", "1001210001", "1001394", "Manhattan"),
            ),
        ]
    )
    assert len(groups) == 1
    assert len(groups[0].labels) == 2


def test_unresolved_bins_on_same_lot_are_never_merged():
    groups = build_groups(
        [
            (
                "main",
                cand("1217 WEST FARMS ROAD", "1217", "WEST FARMS ROAD", "2029930031", "2000000"),
            ),
            (
                "main",
                cand("1223 WEST FARMS ROAD", "1223", "WEST FARMS ROAD", "2029930031", "2000000"),
            ),
            (
                "main",
                cand("1217 WEST FARMS ROAD", "1217", "WEST FARMS ROAD", "2029930031", "2128717"),
            ),
        ]
    )
    assert len(groups) == 3


def test_candidate_with_inconsistent_borough_is_dropped():
    # Borough field says Manhattan but BBL starts with 2 (Bronx): not trusted.
    groups = build_groups(
        [("main", cand("X", "1", "A STREET", "2027540005", "2006185", "Manhattan"))]
    )
    assert groups == []


def test_queens_hyphenations():
    assert queens_hyphenations("7217") == ["72-17"]
    assert queens_hyphenations("20915") == ["209-15"]
    assert queens_hyphenations("311") == ["3-11"]
    assert queens_hyphenations("33-43") == []  # already hyphenated
    assert queens_hyphenations("12") == []  # too short
    assert queens_hyphenations("1A") == []
