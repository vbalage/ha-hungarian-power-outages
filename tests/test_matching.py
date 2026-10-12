"""Address coverage must not overclaim exact household matches."""

import pytest

from custom_components.hungarian_power_outages.models import (
    Address,
    AddressRange,
    MatchLevel,
    city_matches,
    house_number,
    match_range,
)


@pytest.mark.parametrize(
    ("number", "start", "end", "expected"),
    [
        ("14", "2", "20", MatchLevel.ADDRESS),
        ("13", "2", "20", None),
        ("15", "1", "21", MatchLevel.ADDRESS),
        ("14", "1", "21", None),
        ("21", "2", "20", None),
        ("14", "14 HRSZ: 123/4", "", MatchLevel.ADDRESS),
        ("14/A", "14/A", "", MatchLevel.ADDRESS),
        ("14/B", "14/A", "", None),
        ("14/A", "14", "", MatchLevel.UNCERTAIN),
        ("14", "14/A", "", MatchLevel.UNCERTAIN),
        ("14", "HRSZ: 123/4", "", MatchLevel.UNCERTAIN),
        ("14", "", "", MatchLevel.UNCERTAIN),
        ("14", "2", "HRSZ: 123/4", MatchLevel.UNCERTAIN),
        ("14", "20", "2", MatchLevel.UNCERTAIN),
        ("14", "14.", "", MatchLevel.ADDRESS),
        ("14/A", "2", "20", MatchLevel.UNCERTAIN),
        ("14", "12-16", "", MatchLevel.UNCERTAIN),
    ],
)
def test_house_number_ranges(number, start, end, expected):
    assert (
        match_range(
            Address("Mintaváros", "9999", "Példa u.", number),
            AddressRange("Mintavaros", "9999", "Példa utca", start, end),
        )
        == expected
    )


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        (Address("Mintaváros"), MatchLevel.AREA),
        (Address("Mintaváros", "9999", "Példa utca"), MatchLevel.STREET),
        (Address("Mintaváros", "9998", "Példa utca", "14"), None),
        (Address("Mintaváros", "9999", "Példa út", "14"), None),
        (Address("Budapest XII."), None),
    ],
)
def test_location_scope(address, expected):
    assert (
        match_range(address, AddressRange("Mintaváros", "9999", "Példa utca", "2", "20"))
        == expected
    )


def test_parcel_not_a_number():
    assert house_number("HRSZ: 14/2") is None


def test_blank_source_street_is_uncertain():
    assert (
        match_range(
            Address("Mintaváros", "9999", "Példa utca", "14"),
            AddressRange("Mintaváros", "9999", "", "HRSZ: 123/4"),
        )
        == MatchLevel.UNCERTAIN
    )


@pytest.mark.parametrize(
    ("query", "source", "expected"),
    [
        ("Budapest", "Budapest XVI.", True),
        (" BUDAPEST ", "Budapest XXIII.", True),
        ("Budapest XVI.", "Budapest 16. kerület", True),
        ("Budapest 16", "Budapest XVI.", True),
        ("Budapest 016. kerület", "Budapest XVI.", True),
        ("Budapest XVI. ker.", "Budapest XVI.", True),
        ("Budapest I", "Budapest II.", False),
        ("Budapest 1", "Budapest XI.", False),
        ("Budapest XVI.", "Budapest", False),
        ("Budapest", "Budapesti", False),
        ("Mintaváros", "Mintaváros Külterület", True),
        ("Mintaváros", "Másik Mintaváros", False),
        ("Érd", "Erdőkertes", False),
        ("Győr", "Győr-Ménfőcsanak", True),
        ("", "Budapest XVI.", False),
    ],
)
def test_town_and_district_matching(query, source, expected):
    assert city_matches(query, source) is expected
