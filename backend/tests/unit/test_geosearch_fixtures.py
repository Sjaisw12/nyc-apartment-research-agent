"""Parse real GeoSearch responses captured by scripts/smoke_sources.py."""

import pytest

from nyc_apartment_agent.errors import SourceResponseError
from nyc_apartment_agent.http_client import SourceResponse
from nyc_apartment_agent.identifiers import BinStatus
from nyc_apartment_agent.sources.geosearch import parse_features


def test_test_building_is_top_candidate(fixture_loader):
    cands = parse_features(fixture_loader("geosearch", "west_farms_1134"))
    top = cands[0]
    assert top.label == "1134 WEST FARMS ROAD, Bronx, NY, USA"
    assert (top.bbl, top.bin) == ("2027540005", "2006185")
    assert top.bin_status is BinStatus.ASSIGNED
    assert top.pad_version
    assert top.warnings == []


def test_placeholder_bin_is_flagged_not_dropped(fixture_loader):
    cands = parse_features(fixture_loader("geosearch", "east_228_873"))
    garage = [c for c in cands if c.bin == "2000000"]
    real = [c for c in cands if c.bin == "2130638"]
    assert garage and real
    # Same tax lot, two PAD records: the house has a real BIN, the garage a placeholder.
    assert garage[0].bbl == real[0].bbl == "2048530011"
    assert garage[0].bin_status is BinStatus.UNASSIGNED_PLACEHOLDER
    assert any("placeholder" in w for w in garage[0].warnings)


def test_alias_spellings_share_identifiers(fixture_loader):
    cands = parse_features(fixture_loader("geosearch", "centre_st_1"))
    one_centre = [
        c
        for c in cands
        if c.label.endswith("New York, NY, USA")
        and c.label.split(",")[0] in {"1 CENTER STREET", "1 CENTRE STREET"}
    ]
    # Two spellings of the same address: same lot, same building.
    assert len(one_centre) == 2
    assert {(c.bbl, c.bin) for c in one_centre} == {("1001210001", "1001394")}


def test_one_lot_can_hold_several_bins(fixture_loader):
    cands = parse_features(fixture_loader("geosearch", "centre_st_1"))
    bins_on_lot = {c.bin for c in cands if c.bbl == "1001210001"}
    # "1A Centre Street" is on the same tax lot but is a different building (BIN).
    assert bins_on_lot == {"1001394", "1091024"}


def test_borough_in_query_does_not_filter_results(fixture_loader):
    # The query said "Manhattan", yet Bronx candidates come back. The resolver must filter.
    cands = parse_features(fixture_loader("geosearch", "centre_st_1"))
    assert any(c.borough == "Bronx" for c in cands)


def test_address_without_borough_returns_several_boroughs(fixture_loader):
    cands = parse_features(fixture_loader("geosearch", "broadway_100_no_borough"))
    assert len({c.bbl[0] for c in cands if c.bbl}) >= 2


def test_nonsense_query_returns_no_candidates(fixture_loader):
    assert parse_features(fixture_loader("geosearch", "nonsense")) == []


def _resp(data):
    return SourceResponse(
        source="geosearch",
        url="u",
        status_code=200,
        data=data,
        headers={},
        attempts=1,
        elapsed_seconds=0.0,
    )


def test_not_a_feature_collection_is_rejected():
    with pytest.raises(SourceResponseError):
        parse_features(_resp({"error": "x"}))


def test_feature_without_geometry_is_rejected():
    with pytest.raises(SourceResponseError):
        parse_features(
            _resp({"type": "FeatureCollection", "features": [{"properties": {"label": "x"}}]})
        )


def test_missing_and_malformed_identifiers_become_warnings():
    feature = {
        "geometry": {"coordinates": [-73.9, 40.8]},
        "properties": {"label": "X", "addendum": {"pad": {"bbl": "123", "bin": "9999999"}}},
    }
    (cand,) = parse_features(_resp({"type": "FeatureCollection", "features": [feature]}))
    assert cand.bbl is None and cand.bin is None
    assert cand.bin_status is BinStatus.MISSING
    assert len(cand.warnings) == 2


def test_borough_mismatch_warning():
    feature = {
        "geometry": {"coordinates": [-73.9, 40.8]},
        "properties": {"label": "X", "addendum": {"pad": {"bbl": "1001210001", "bin": "2006185"}}},
    }
    (cand,) = parse_features(_resp({"type": "FeatureCollection", "features": [feature]}))
    assert "BBL and BIN borough digits disagree" in cand.warnings
