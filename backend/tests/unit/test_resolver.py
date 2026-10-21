"""Resolver regression tests, offline, on real responses recorded from the live APIs."""

import csv
import json
from pathlib import Path

import httpx
import pytest

from nyc_apartment_agent.cassette import ReplayTransport, load_cassette
from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.resolver import Outcome, ResolutionLevel, resolve_address
from nyc_apartment_agent.resolver.models import BinEvidence, Reason
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

ROOT = Path(__file__).resolve().parents[3]
CASSETTE = ROOT / "backend" / "tests" / "fixtures" / "benchmark" / "resolver_cassette.json"
ROWS = {
    r["id"]: r
    for r in csv.DictReader(
        (ROOT / "data" / "eval" / "candidate_addresses_draft.csv").open(encoding="utf-8")
    )
}

# Rows where the resolver's outcome differs from the approved label. Each one is reported,
# not hidden: see data/eval/benchmark_results.md.
KNOWN_DEVIATIONS = {
    # PAD lists "3343 14 STREET" as an official alias of 33-43 14 Street, so the typed number
    # matches exactly and the building is correct, but the label expected "ambiguous".
    "A09": "verified",
}


@pytest.fixture(scope="module")
def entries():
    return load_cassette(CASSETTE)


def run(text, entries, drop=(), patch=None):
    data = {k: v for k, v in entries.items() if not any(d in k for d in drop)}
    if patch:
        data = {k: (patch(k, v) or v) for k, v in data.items()}
    transport = ReplayTransport(data)
    settings = Settings(_env_file=None)
    with geosearch_client(settings, transport) as g, socrata_client(settings, transport) as s:
        return resolve_address(text, g, s), transport


@pytest.mark.parametrize("row_id", sorted(ROWS))
def test_benchmark_row(row_id, entries):
    row = ROWS[row_id]
    result, transport = run(row["query"], entries)
    assert transport.misses == []
    expected = KNOWN_DEVIATIONS.get(row_id, row["expected_behavior"])
    assert result.outcome.value == expected, result.explanation
    exp_bbls = [b for b in row["proposed_bbl"].split("|") if b]
    exp_bins = [b for b in row["proposed_bin"].split("|") if b]
    if result.outcome is Outcome.VERIFIED:
        assert result.building.bbl in exp_bbls  # never the wrong building
        if exp_bins:
            assert result.building.bin in exp_bins
        else:
            assert result.building.bin is None
    if result.outcome is Outcome.AMBIGUOUS:
        assert set(exp_bbls) <= {o.bbl for o in result.options}
    assert result.explanation


def test_99999_broadway_is_rejected_despite_five_candidates(entries):
    result, _ = run("99999 Broadway, New York, NY", entries)
    assert result.outcome is Outcome.NO_RELIABLE_MATCH
    assert Reason.HOUSE_NUMBER_MISMATCH in result.reasons
    assert len(result.candidates) >= 4  # GeoSearch did return Broadway buildings
    assert all(c.number.value == "mismatch" for c in result.candidates)
    assert result.building is None and result.options == []
    assert result.permitted_join_keys == []


def test_99999_broadway_without_borough_checks_all_five_boroughs(entries):
    result, _ = run("99999 Broadway", entries)
    assert result.outcome is Outcome.NO_RELIABLE_MATCH
    assert len(result.provenance["requests"]) == 6  # main query + 5 borough checks


def test_placeholder_bin_gives_tax_lot_level_only(entries):
    result, _ = run(ROWS["E13"]["query"], entries)
    assert result.outcome is Outcome.VERIFIED
    assert result.resolution_level is ResolutionLevel.TAX_LOT
    assert result.building.bin is None
    assert result.building.bin_evidence is BinEvidence.PLACEHOLDER
    assert result.permitted_join_keys == ["bbl"]
    assert result.building_specific_findings_allowed is False
    # Footprints shows a building on the lot, but it is context, never the identifier.
    assert result.building.footprint_bins_on_lot == ["2128717"]


def test_bin_lot_conflict_stays_unresolved_and_ambiguous(entries):
    result, _ = run(ROWS["E14"]["query"], entries)
    assert result.outcome is Outcome.AMBIGUOUS
    assert Reason.BIN_LOT_CONFLICT in result.reasons
    assert [o.bin for o in result.options] == [None]
    assert result.permitted_join_keys == []


def test_garage_placeholder_record_is_not_chosen(entries):
    result, _ = run(ROWS["E12"]["query"], entries)
    assert result.building.bin == "2130638"
    assert result.resolution_level is ResolutionLevel.BUILDING
    assert result.permitted_join_keys == ["bbl", "bin"]


def test_condo_billing_lot_cross_verifies(entries):
    result, _ = run(ROWS["E15"]["query"], entries)
    assert result.resolution_level is ResolutionLevel.BUILDING
    assert result.building.bbl == "5005797501"


def test_borough_inferred_when_unique(entries):
    result, _ = run("530 2nd Street", entries)
    assert result.outcome is Outcome.VERIFIED
    assert result.building.borough == "Brooklyn"
    assert result.building.borough_source == "inferred"
    assert Reason.BOROUGH_INFERRED in result.reasons


def test_failed_borough_check_blocks_unique_claim(entries):
    # Drop the Queens check: Brooklyn still matches, but uniqueness is unproven.
    result, _ = run("530 2nd Street", entries, drop=["530+2nd+Street%2C+Queens"])
    assert result.outcome is Outcome.AMBIGUOUS
    assert Reason.BOROUGH_CHECK_INCOMPLETE in result.reasons


def test_failed_borough_check_with_no_match_is_source_unavailable(entries):
    result, _ = run("99999 Broadway", entries, drop=["99999+Broadway%2C+Bronx"])
    assert result.outcome is Outcome.SOURCE_UNAVAILABLE
    assert result.outcome is not Outcome.NO_RELIABLE_MATCH


def test_zip_conflict_is_ambiguous(entries):
    result, _ = run("530 2nd Street, Brooklyn, NY 10001", entries)
    assert result.outcome is Outcome.AMBIGUOUS
    assert Reason.ZIP_CONFLICT in result.reasons


def test_geosearch_down_is_source_unavailable_not_no_match():
    result, _ = run(ROWS["N01"]["query"], {})
    assert result.outcome is Outcome.SOURCE_UNAVAILABLE
    assert Reason.GEOSEARCH_UNAVAILABLE in result.reasons
    assert result.permitted_join_keys == []


def test_footprints_down_limits_result_to_tax_lot(entries):
    result, _ = run(ROWS["N01"]["query"], entries, drop=["5zhs-2jue"])
    assert result.outcome is Outcome.VERIFIED
    assert result.resolution_level is ResolutionLevel.TAX_LOT
    assert result.building.bin_evidence is BinEvidence.NOT_CHECKED
    assert result.permitted_join_keys == ["bbl"]


def test_pad_only_bin_is_weaker_than_cross_verified(entries):
    def empty_footprints(url, entry):
        if "5zhs-2jue" in url:
            return {**entry, "body": "[]"}
        return None

    result, _ = run(ROWS["N01"]["query"], entries, patch=empty_footprints)
    assert result.outcome is Outcome.VERIFIED
    assert result.building.bin_evidence is BinEvidence.PAD_ONLY
    assert result.resolution_level is ResolutionLevel.TAX_LOT
    assert result.permitted_join_keys == ["bbl"]
    assert "not cross-verified" in result.explanation


def _geosearch_body(features):
    return json.dumps({"type": "FeatureCollection", "features": features})


def _feature(number, street, bbl, bin_value, borough="Bronx"):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [-73.9, 40.8]},
        "properties": {
            "label": f"{number} {street}, {borough}, NY, USA",
            "housenumber": number,
            "street": street,
            "borough": borough,
            "postalcode": "10459",
            "addendum": {"pad": {"bbl": bbl, "bin": bin_value, "version": "26c"}},
        },
    }


def test_competing_real_and_placeholder_records_are_ambiguous():
    body = _geosearch_body(
        [
            _feature("10", "TEST STREET", "2027540005", "2006185"),
            _feature("10", "TEST STREET", "2027540005", "2000000"),
        ]
    )

    def handler(request):
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)
    settings = Settings(_env_file=None)
    with geosearch_client(settings, transport) as g, socrata_client(settings, transport) as s:
        result = resolve_address("10 Test Street, Bronx", g, s)
    assert result.outcome is Outcome.AMBIGUOUS
    assert Reason.MULTIPLE_BUILDINGS in result.reasons
    assert {o.bin for o in result.options} == {"2006185", None}


@pytest.mark.parametrize("text", ["", "   ", "x" * 201])
def test_bad_input_makes_no_network_calls(text):
    def handler(request):
        raise AssertionError("network must not be called")

    transport = httpx.MockTransport(handler)
    settings = Settings(_env_file=None)
    with geosearch_client(settings, transport) as g, socrata_client(settings, transport) as s:
        result = resolve_address(text, g, s)
    assert result.outcome is Outcome.NO_RELIABLE_MATCH


def test_stated_borough_follow_up_failure_is_source_unavailable(entries):
    result, _ = run(
        "530 2nd Street, Brooklyn, NY 10001", entries, drop=["530+2nd+Street%2C+Brooklyn&"]
    )
    assert result.outcome is Outcome.SOURCE_UNAVAILABLE
