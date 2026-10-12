"""Parcel identifiers must match in full within the selected settlement."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from custom_components.hungarian_power_outages.config_flow import cleaned, unique_id
from custom_components.hungarian_power_outages.const import DOMAIN
from custom_components.hungarian_power_outages.diagnostics import async_get_config_entry_diagnostics
from custom_components.hungarian_power_outages.models import (
    Address,
    AddressRange,
    MatchLevel,
    match_range,
    parcel_number,
)
from custom_components.hungarian_power_outages.providers.eon import FAULTS_URL, PLANNED_URL

from .test_integration import DATA, entity_id, setup
from .test_provider import FAULTS, NOW, PLANNED


@pytest.fixture(autouse=True)
def fixed_time(freezer):
    freezer.move_to(NOW)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0123/4", "0123/4"),
        (" HRSZ: 0123 / 4 ", "0123/4"),
        ("538/b", "538/B"),
        ("43497/5/A/3", "43497/5/A/3"),
        ("1.Hrsz", "1"),
        ("973/2 hrsz.", "973/2"),
        ("Hrsz. 123/4.", "123/4"),
        ("123-130", None),
        ("123/4, 123/5", None),
        ("123/4 II. emelet 2.", None),
        ("123//4", None),
        ("123 4", None),
        ("", None),
    ],
)
def test_parcel_normalization(value, expected):
    assert parcel_number(value) == expected


@pytest.mark.parametrize(
    ("street", "start", "end", "expected"),
    [
        ("Példa utca", "14 HRSZ: 0123/4", "", MatchLevel.PARCEL),
        ("", "HRSZ: 0123/4", "", MatchLevel.PARCEL),
        ("Hrsz.", "0123/4", "", MatchLevel.PARCEL),
        ("Példa utca", "0123/4 hrsz.", "", MatchLevel.PARCEL),
        ("Példa utca", "HRSZ: 0123/3", "HRSZ: 0123/4", MatchLevel.PARCEL),
        ("Példa utca", "HRSZ: 123/4", "", None),
        ("Példa utca", "HRSZ: 0123/40", "", None),
        ("Példa utca", "HRSZ: 0123", "", None),
        ("Példa utca", "14", "", None),
        ("Példa utca", "0123/4", "", None),
        ("Példa utca", "HRSZ: 0123/4-0123/9", "", None),
    ],
)
def test_exact_parcel_matching(street, start, end, expected):
    assert (
        match_range(
            Address("Mintaváros", parcel_number="0123/4"),
            AddressRange("Mintaváros", "9999", street, start, end),
        )
        == expected
    )


def test_settlement_scope_and_conflicting_address():
    address = Address("Mintaváros", "9999", "Eltérő utca", "99", "0123/4")
    assert match_range(address, AddressRange("Másikváros", "9999", "", "HRSZ: 0123/4")) is None
    assert (
        match_range(address, AddressRange("Mintaváros", "9999", "Példa utca", "HRSZ: 0123/4"))
        == MatchLevel.PARCEL
    )
    assert (
        match_range(address, AddressRange("Mintaváros", "9999", "Eltérő utca", "99 HRSZ: 0123/5"))
        is None
    )
    assert (
        match_range(address, AddressRange("Mintaváros", "9999", "Eltérő utca", "99"))
        == MatchLevel.ADDRESS
    )
    assert (
        match_range(
            address, AddressRange("Mintaváros", "9999", "Eltérő utca", "HRSZ: 0123/4-0123/9")
        )
        == MatchLevel.UNCERTAIN
    )


def test_identity_preserves_existing_addresses_and_separates_parcels():
    assert (
        Address("Mintaváros", "9999", "Példa utca", "14").identity
        == "mintavaros|9999|pelda utca|14"
    )
    first = cleaned({**DATA, "parcel_number": "HRSZ: 0123/4"})
    assert unique_id(first) == unique_id(cleaned({**DATA, "parcel_number": "0123 / 4"}))
    assert unique_id(first) != unique_id(cleaned({**DATA, "parcel_number": "123/4"}))


async def test_parcel_only_setup_entities_and_redacted_diagnostics(hass, aioclient_mock):
    planned = deepcopy(PLANNED)
    planned["outages"][0]["addressRanges"][0]["street"] = ""
    planned["outages"][0]["addressRanges"][0]["startNum"] = "HRSZ: 0123/4"
    planned["outages"][0]["addressRanges"][0]["endNum"] = None
    entry = await setup(
        hass, aioclient_mock, planned=planned, street="", house_number="", parcel_number="0123/4"
    )
    state = hass.states.get(entity_id(hass, entry, "sensor", "planned_count"))
    assert state.state == "1"
    assert state.attributes["outages"][0]["match"] == "parcel"
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert "0123/4" not in str(diagnostics)
    count_id = entity_id(hass, entry, "sensor", "planned_count")
    flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {**DATA, "street": "", "house_number": "", "parcel_number": "0123/5"}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entity_id(hass, entry, "sensor", "planned_count") == count_id
    assert hass.states.get(count_id).state == "0"


async def test_config_flow_accepts_parcel_without_street(hass, aioclient_mock):
    aioclient_mock.get(PLANNED_URL, json=PLANNED)
    aioclient_mock.get(FAULTS_URL, json=FAULTS)
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    with patch("custom_components.hungarian_power_outages.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"],
            {**DATA, "street": "", "house_number": "", "parcel_number": "HRSZ: 0123/4"},
        )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert result["data"]["parcel_number"] == "0123/4"
    assert "HRSZ 0123/4" in result["title"]


@pytest.mark.parametrize("parcel", ["123-130", "123/4,123/5", "123/4 II. 2."])
async def test_invalid_parcel_input(hass, parcel):
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {**DATA, "parcel_number": parcel}
    )
    assert result["errors"] == {"base": "invalid_address"}


async def test_budapest_includes_districts_for_parcels_and_faults(hass, aioclient_mock):
    planned = deepcopy(PLANNED)
    for target in planned["outages"][0]["addressRanges"]:
        target["city"] = "Budapest XVI."
        target["startNum"] = "HRSZ: 0123/4"
        target["endNum"] = None
    faults = deepcopy(FAULTS)
    faults["outages"][0]["city"] = "Budapest XI."
    entry = await setup(
        hass,
        aioclient_mock,
        planned=planned,
        faults=faults,
        city="Budapest",
        street="",
        house_number="",
        parcel_number="0123/4",
    )
    count = hass.states.get(entity_id(hass, entry, "sensor", "planned_count"))
    assert count.state == "1"
    assert count.attributes["outages"][0]["match"] == "parcel"
    assert hass.states.get(entity_id(hass, entry, "sensor", "fault_reports")).state == "1"
