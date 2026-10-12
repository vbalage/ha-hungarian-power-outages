"""Exercise setup, entities, time boundaries, refresh and unload against real HA."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.hungarian_power_outages.const import DOMAIN
from custom_components.hungarian_power_outages.diagnostics import async_get_config_entry_diagnostics
from custom_components.hungarian_power_outages.providers.eon import FAULTS_URL, PLANNED_URL

from .test_provider import FAULTS, NOW, PLANNED

DATA = {
    "provider": "eon",
    "city": "Mintaváros",
    "postcode": "9999",
    "street": "Példa utca",
    "house_number": "14",
}


@pytest.fixture(autouse=True)
def fixed_time(freezer):
    freezer.move_to(NOW)


def entry_for(hass, **overrides):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test address",
        data={**DATA, **overrides},
        unique_id="test-address-" + overrides.get("house_number", "14"),
    )
    entry.add_to_hass(hass)
    return entry


def entity_id(hass, entry, platform, key):
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}")


async def setup(hass, aioclient_mock, planned=PLANNED, faults=FAULTS, **overrides):
    aioclient_mock.get(PLANNED_URL, json=planned)
    aioclient_mock.get(FAULTS_URL, json=faults)
    await async_setup_component(hass, "homeassistant", {})
    entry = entry_for(hass, **overrides)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_entities_calendar_and_unload(hass, aioclient_mock):
    entry = await setup(hass, aioclient_mock)
    assert len(er.async_get(hass).entities.get_entries_for_config_entry_id(entry.entry_id)) == 13
    count = entity_id(hass, entry, "sensor", "planned_count")
    assert hass.states.get(count).state == "1"
    assert hass.states.get(count).attributes["outages"][0]["match"] == "address"
    reports = entity_id(hass, entry, "sensor", "fault_reports")
    assert hass.states.get(reports).state == "1"
    assert not hass.states.get(reports).attributes["household_coverage_confirmed"]
    calendar = entity_id(hass, entry, "calendar", "planned_calendar")
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
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(count).state == "unavailable"
    assert hass.states.get(count).attributes.get("restored")
    assert not hass.data.get(DOMAIN)


async def test_time_boundaries_advance_without_polling(hass, aioclient_mock, freezer):
    payload = deepcopy(PLANNED)
    payload["outages"][0]["intervals"][0] = {
        "from": {"date": "2026-10-12", "time": "10:31:00"},
        "to": {"date": "2026-10-12", "time": "10:33:00"},
        "isCancelled": False,
    }
    entry = await setup(hass, aioclient_mock, planned=payload)
    binary = entity_id(hass, entry, "binary_sensor", "planned_now")
    assert hass.states.get(binary).state == "off"
    freezer.move_to(NOW + timedelta(minutes=1))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get(binary).state == "on"
    freezer.move_to(NOW + timedelta(minutes=3))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get(binary).state == "off"
    assert aioclient_mock.call_count == 2


async def test_cancelled_not_counted_or_in_calendar(hass, aioclient_mock):
    payload = deepcopy(PLANNED)
    payload["outages"][0]["intervals"][0]["isCancelled"] = True
    entry = await setup(hass, aioclient_mock, planned=payload)
    assert hass.states.get(entity_id(hass, entry, "sensor", "planned_count")).state == "0"
    assert entry.runtime_data.matched_planned() == []


async def test_partial_failure_and_recovery(hass, aioclient_mock, freezer):
    aioclient_mock.get(PLANNED_URL, status=503)
    aioclient_mock.get(FAULTS_URL, json=FAULTS)
    entry = entry_for(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id(hass, entry, "sensor", "planned_count")).state == "unavailable"
    assert (
        hass.states.get(entity_id(hass, entry, "sensor", "planned_health")).state
        == "cannot_connect"
    )
    assert hass.states.get(entity_id(hass, entry, "sensor", "fault_reports")).state == "1"
    assert entry.runtime_data.update_interval == timedelta(minutes=15)
    aioclient_mock.clear_requests()
    aioclient_mock.get(PLANNED_URL, json=PLANNED)
    aioclient_mock.get(FAULTS_URL, json=FAULTS)
    with patch(
        "custom_components.hungarian_power_outages.providers.eon.monotonic", return_value=10**12
    ):
        await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id(hass, entry, "sensor", "planned_count")).state == "1"
    assert entry.runtime_data.update_interval == timedelta(hours=24)


async def test_stale_feed_and_redacted_diagnostics(hass, aioclient_mock):
    payload = deepcopy(PLANNED)
    payload["generatedAt"]["date"] = "2026-10-01"
    entry = await setup(hass, aioclient_mock, planned=payload)
    assert hass.states.get(entity_id(hass, entry, "sensor", "planned_count")).state == "unavailable"
    assert hass.states.get(entity_id(hass, entry, "sensor", "planned_health")).state == "stale"
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    rendered = str(diagnostics)
    for private in ("Példa", "9999", entry.entry_id, "test-planned", "coordinates"):
        assert private not in rendered


async def test_manual_refresh_and_cache_sharing(hass, aioclient_mock):
    first = await setup(hass, aioclient_mock)
    second = entry_for(hass, house_number="16")
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    assert first.runtime_data.client is second.runtime_data.client
    assert aioclient_mock.call_count == 2
    button = entity_id(hass, first, "button", "refresh")
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    assert aioclient_mock.call_count == 2
    with patch(
        "custom_components.hungarian_power_outages.providers.eon.monotonic", return_value=10**12
    ):
        await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    assert aioclient_mock.call_count == 4
    assert await hass.config_entries.async_unload(first.entry_id)
    await hass.async_block_till_done()
    assert DOMAIN in hass.data and hass.data[DOMAIN]
    assert await hass.config_entries.async_unload(second.entry_id)
    await hass.async_block_till_done()
    assert not hass.data[DOMAIN]


async def test_config_flow_and_duplicate_address(hass, aioclient_mock):
    aioclient_mock.get(PLANNED_URL, json=PLANNED)
    aioclient_mock.get(FAULTS_URL, json=FAULTS)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    with patch("custom_components.hungarian_power_outages.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    await hass.async_block_till_done()
    assert result["type"] == "create_entry" and result["data"] == {**DATA, "nearby_enabled": False}
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        again["flow_id"], {**DATA, "street": "Pelda u."}
    )
    assert result["type"] == "abort" and result["reason"] == "already_configured"


@pytest.mark.parametrize(
    "changes", [{"city": " "}, {"street": "", "house_number": "14"}, {"postcode": "203"}]
)
async def test_invalid_address_flow(hass, changes):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**DATA, **changes})
    assert result["errors"] == {"base": "invalid_address"}


async def test_options_reload(hass, aioclient_mock):
    entry = await setup(hass, aioclient_mock)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"poll_hours": 12, "stale_hours": 72}
    )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert entry.runtime_data.update_interval == timedelta(hours=12)
    assert entry.runtime_data.stale_hours == 72


async def test_address_reconfigure_preserves_entity_ids(hass, aioclient_mock):
    entry = await setup(hass, aioclient_mock)
    before = entity_id(hass, entry, "sensor", "planned_count")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**DATA, "house_number": "15"}
    )
    await hass.async_block_till_done()
    assert result["type"] == "abort" and result["reason"] == "reconfigure_successful"
    assert entry.data["house_number"] == "15"
    assert entity_id(hass, entry, "sensor", "planned_count") == before
    assert hass.states.get(before).state == "0"


async def test_incomplete_interval_affects_only_matching_location(hass, aioclient_mock):
    payload = deepcopy(PLANNED)
    payload["outages"][0]["intervals"][0]["to"] = payload["outages"][0]["intervals"][0]["from"]
    entry = await setup(hass, aioclient_mock, planned=payload)
    assert hass.states.get(entity_id(hass, entry, "sensor", "planned_count")).state == "unavailable"
    assert hass.states.get(entity_id(hass, entry, "sensor", "planned_health")).state == "degraded"
    other = entry_for(hass, city="Másikváros", house_number="16")
    assert await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id(hass, other, "sensor", "planned_count")).state == "0"


async def test_stale_calendar_does_not_return_old_events(hass, aioclient_mock):
    from homeassistant.exceptions import HomeAssistantError

    payload = deepcopy(PLANNED)
    payload["generatedAt"]["date"] = "2026-10-01"
    entry = await setup(hass, aioclient_mock, planned=payload)
    calendar = entity_id(hass, entry, "calendar", "planned_calendar")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
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


async def test_staleness_changes_locally(hass, aioclient_mock, freezer):
    entry = await setup(hass, aioclient_mock)
    entry.runtime_data._tick(NOW + timedelta(hours=49))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id(hass, entry, "sensor", "planned_count")).state == "unavailable"
    assert hass.states.get(entity_id(hass, entry, "sensor", "planned_health")).state == "stale"
    assert aioclient_mock.call_count == 2


@pytest.mark.parametrize("status", [503, 200])
async def test_setup_flow_failure(hass, aioclient_mock, status):
    for url in (PLANNED_URL, FAULTS_URL):
        aioclient_mock.get(url, status=status, json={"unsupported": []})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["type"] == "form"
    assert result["errors"]["base"] == ("cannot_connect" if status == 503 else "invalid_feed")
