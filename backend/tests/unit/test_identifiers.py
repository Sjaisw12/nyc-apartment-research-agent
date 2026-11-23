import pytest

from nyc_apartment_agent.errors import InvalidIdentifierError
from nyc_apartment_agent.identifiers import (
    BinStatus,
    borough_of,
    classify_bin,
    normalize_bbl,
    normalize_bin,
)


def test_valid_bbl_and_bin():
    assert normalize_bbl(" 2027540005 ") == "2027540005"
    assert normalize_bin(2006185) == "2006185"
    assert borough_of("2027540005") == "Bronx"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "202754000",
        "20275400055",
        "6027540005",
        "0027540005",
        "2O27540005",
        "2027540005' OR '1'='1",
        "2000000005",
    ],
)
def test_invalid_bbl_rejected(bad):
    with pytest.raises(InvalidIdentifierError):
        normalize_bbl(bad)


@pytest.mark.parametrize("bad", ["200618", "20061855", "6006185", "abcdefg", "2006185;"])
def test_invalid_bin_rejected(bad):
    with pytest.raises(InvalidIdentifierError):
        normalize_bin(bad)


@pytest.mark.parametrize("placeholder", ["1000000", "2000000", "3000000", "4000000", "5000000"])
def test_borough_placeholder_bins(placeholder):
    assert classify_bin(placeholder) is BinStatus.UNASSIGNED_PLACEHOLDER


def test_other_bins():
    assert classify_bin("2006185") is BinStatus.ASSIGNED
    assert classify_bin("2000001") is BinStatus.ASSIGNED
    assert classify_bin(None) is BinStatus.MISSING
    assert classify_bin("") is BinStatus.MISSING
