import pytest

from nyc_apartment_agent.resolver.normalize import (
    edit_distance,
    normalize_street,
    parse_address,
)


@pytest.mark.parametrize(
    "text,number,street,borough,zip_code",
    [
        ("1134 West Farms Rd, Bronx, NY 10459", "1134", "WEST FARMS ROAD", "Bronx", "10459"),
        ("350 5th Avenue, New York, NY 10118", "350", "5 AVENUE", "Manhattan", "10118"),
        ("33-43 14th Street, Astoria, NY 11106", "33-43", "14 STREET", None, "11106"),
        ("1279 First Avenue, Manhattan", "1279", "1 AVENUE", "Manhattan", None),
        ("1A Centre Street, Manhattan", "1A", "CENTRE STREET", "Manhattan", None),
        ("1134 West Farms Rd Bronx", "1134", "WEST FARMS ROAD", "Bronx", None),
        ("530 2nd Street 11215", "530", "2 STREET", None, "11215"),
        ("50 Belair Road, 10305", "50", "BELAIR ROAD", None, "10305"),
        ("1 Main St, Kings County", "1", "MAIN STREET", "Brooklyn", None),
    ],
)
def test_parse(text, number, street, borough, zip_code):
    p = parse_address(text)
    assert (p.house_number, p.street_normalized, p.borough, p.zip_code) == (
        number,
        street,
        borough,
        zip_code,
    )


def test_house_number_is_never_read_as_zip():
    p = parse_address("99999 Broadway, New York, NY")
    assert p.house_number == "99999"
    assert p.zip_code is None


def test_missing_house_number():
    p = parse_address("Broadway, Manhattan")
    assert p.house_number is None
    assert "no house number found" in p.notes


def test_neighborhood_is_not_treated_as_borough():
    p = parse_address("209-15 18th Avenue, Bayside, NY 11360")
    assert p.borough is None
    assert any("Bayside" in n for n in p.notes)


def test_out_of_state_detected():
    assert parse_address("1600 Pennsylvania Avenue NW, Washington, DC 20500").state == "DC"


@pytest.mark.parametrize(
    "a,b",
    [
        ("W 42nd St", "WEST 42 STREET"),
        ("West Forty", "WEST FORTY"),
        ("Second Ave", "2 AVENUE"),
        ("E. 228th St.", "EAST 228 STREET"),
        ("St Nicholas Ave", "ST NICHOLAS AVENUE"),  # leading "St" is a saint, not "Street"
        ("B'WAY", "BROADWAY"),
    ],
)
def test_normalize_street(a, b):
    assert normalize_street(a) == b


def test_edit_distance():
    assert edit_distance("2 STRET", "2 STREET") == 1
    assert edit_distance("BROADWAY", "BROADWAY") == 0
    assert edit_distance("ABCDEFGH", "ZZZZZZZZ", limit=2) == 3
