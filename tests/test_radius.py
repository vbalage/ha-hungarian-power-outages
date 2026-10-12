"""Radius searches show candidates without changing address coverage."""

from copy import deepcopy

import pytest

from custom_components.hungarian_power_outages.const import DOMAIN
from custom_components.hungarian_power_outages.models import Point, SearchArea
from custom_components.hungarian_power_outages.providers.eon import area_radius, parse_feed, point

from .test_integration import DATA, entity_id, setup
from .test_provider import FAULTS, NOW, PLANNED

LOCATION = {"latitude": 47.0, "longitude": 19.0, "radius": 1000.0}


@pytest.fixture(autouse=True)
def fixed_time(freezer):
    freezer.move_to(NOW)


def test_distance_and_boundaries():
    center = Point(47, 19)
    assert center.distance(center) == 0
    assert 750 < center.distance(Point(47, 19.01)) < 770
    assert SearchArea(center, center.distance(Point(47, 19.01))).includes(Point(47, 19.01))
    assert not SearchArea(center, 1000).includes(Point(47, 19.02))
    assert SearchArea(center, 1000).includes(Point(47, 19.02), 600)
    assert not SearchArea(center, 1000).includes(None)


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"lat": 0, "lng": 0},
        {"lat": 45.03, "lng": 10.78},
        {"lat": float("nan"), "lng": 19},
        {"lat": float("inf"), "lng": 19},
        {"lat": True, "lng": 19},
        {"lat": 47, "lng": "bad"},
    ],
)
def test_invalid_source_coordinate_not_matched(value):
    assert point(value) is None


@pytest.mark.parametrize("value", [None, -1, 0, float("nan"), float("inf"), True, 100001, "bad"])
def test_invalid_provider_radius_not_used(value):
    assert area_radius(value) == 0


def test_provider_geometry_matches_website_semantics():
    payload = deepcopy(FAULTS)
    record = payload["outages"][0]
    record["coordinates"] = {"lat": 47, "lng": 19.02}
    record["transformerAreaCenterCoordinates"] = {"lat": 0, "lng": 0}
    record["transformerAreaRadius"] = 600
    item = parse_feed(payload, "faults", NOW).items[0]
    assert item.coordinates == Point(47, 19.02)
    assert item.area_radius == 600


async def test_radius_keeps_addressless_outages_and_cross_town_faults(hass, aioclient_mock):
    planned = deepcopy(PLANNED)
    planned["outages"][0]["addressRanges"] = []
    planned["outages"][0]["coordinates"] = {"lat": 47, "lng": 19.01}
    faults = deepcopy(FAULTS)
    faults["outages"][0]["city"] = "Másikváros"
    faults["outages"][0]["coordinates"] = {"lat": 47, "lng": 19.02}
    faults["outages"][0]["transformerAreaRadius"] = 600
    entry = await setup(hass, aioclient_mock, planned=planned, faults=faults, location=LOCATION)
    # Neither a nearby marker nor an overlapping circle makes an address match.
    assert hass.states.get(entity_id(hass, entry, "sensor", "planned_count")).state == "0"
    assert hass.states.get(entity_id(hass, entry, "sensor", "fault_reports")).state == "0"
    nearby = hass.states.get(entity_id(hass, entry, "sensor", "nearby_planned_count"))
    assert nearby.state == "1"
    assert nearby.attributes["radius_m"] == LOCATION["radius"]
    assert nearby.attributes["search_center"] == {"latitude": 47.0, "longitude": 19.0}
    assert nearby.attributes["reports"][0]["latitude"] == 47
    assert nearby.attributes["reports"][0]["longitude"] == 19.01
    assert nearby.attributes["reports"][0]["match"] == "nearby"
    assert not nearby.attributes["reports"][0]["address_information_available"]
    assert not nearby.attributes["household_coverage_confirmed"]
    fault = hass.states.get(entity_id(hass, entry, "sensor", "nearby_fault_reports"))
    assert fault.state == "1"
    assert fault.attributes["reports"][0]["basis"] == "provider_area_overlap"
    assert fault.attributes["reports"][0]["distance_m"] > LOCATION["radius"]
    calendar = entity_id(hass, entry, "calendar", "nearby_planned_calendar")
    events = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": calendar,
            "start_date_time": "2026-10-13T00:00:00+02:00",
            "end_date_time": "2026-10-14T00:00:00+02:00",
        },
        blocking=True,
        return_response=True,
    )
    assert len(events[calendar]["events"]) == 1
    assert "household coverage is not confirmed" in events[calendar]["events"][0]["description"]


async def test_far_markers_and_bad_coordinates_are_excluded(hass, aioclient_mock):
    planned = deepcopy(PLANNED)
    planned["outages"][0]["coordinates"] = {"lat": 47, "lng": 19.03}
    faults = deepcopy(FAULTS)
    faults["outages"][0]["coordinates"] = {"lat": 0, "lng": 0}
    entry = await setup(hass, aioclient_mock, planned=planned, faults=faults, location=LOCATION)
    # Direct address match remains, even if its map marker is far away.
    assert hass.states.get(entity_id(hass, entry, "sensor", "planned_count")).state == "1"
    assert hass.states.get(entity_id(hass, entry, "sensor", "nearby_planned_count")).state == "0"
    assert hass.states.get(entity_id(hass, entry, "sensor", "nearby_fault_reports")).state == "0"


async def test_incomplete_nearby_interval_is_unavailable(hass, aioclient_mock):
    planned = deepcopy(PLANNED)
    planned["outages"][0]["addressRanges"] = []
    planned["outages"][0]["coordinates"] = {"lat": 47, "lng": 19.01}
    planned["outages"][0]["intervals"][0]["to"] = planned["outages"][0]["intervals"][0]["from"]
    entry = await setup(hass, aioclient_mock, planned=planned, location=LOCATION)
    assert (
        hass.states.get(entity_id(hass, entry, "sensor", "nearby_planned_count")).state
        == "unavailable"
    )
    assert hass.states.get(entity_id(hass, entry, "sensor", "planned_count")).state == "0"


async def test_radius_can_be_reconfigured_and_removed(hass, aioclient_mock):
    entry = await setup(hass, aioclient_mock, location=LOCATION)
    count_id = entity_id(hass, entry, "sensor", "planned_count")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**DATA, "nearby_enabled": True, "location": {**LOCATION, "radius": 2000}},
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.runtime_data.search_area.radius == 2000
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**DATA, "nearby_enabled": False}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.runtime_data.search_area is None
    assert "location" not in entry.data
    assert entity_id(hass, entry, "sensor", "planned_count") == count_id


@pytest.mark.parametrize(
    "location",
    [
        {**LOCATION, "radius": 0},
        {**LOCATION, "radius": 100001},
        {**LOCATION, "latitude": 91},
        {**LOCATION, "longitude": 181},
        {**LOCATION, "radius": float("nan")},
    ],
)
async def test_invalid_radius_configuration(hass, location):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**DATA, "nearby_enabled": True, "location": location}
    )
    assert result["errors"] == {"base": "invalid_address"}


async def test_enabled_radius_requires_map_location(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**DATA, "nearby_enabled": True}
    )
    assert result["errors"] == {"base": "invalid_address"}
