"""Regression checks for main-loop stalls and snapshot cache invalidation."""

import asyncio
import threading
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

import pytest

from custom_components.hungarian_power_outages import coordinator as coordinator_module
from custom_components.hungarian_power_outages.models import Address, Point, SearchArea
from custom_components.hungarian_power_outages.providers import eon

from .test_integration import entity_id, setup
from .test_provider import FAULTS, NOW, PLANNED
from .test_radius import LOCATION


@pytest.fixture(autouse=True)
def fixed_time(freezer):
    freezer.move_to(NOW)


async def test_matching_and_parsing_run_off_loop_and_ticks_use_cache(hass, aioclient_mock):
    main_thread = threading.get_ident()
    matched_threads, parsed_threads = [], []
    matcher, parser = coordinator_module.match_snapshot, eon.parse_response

    def record_matching(*args):
        matched_threads.append(threading.get_ident())
        return matcher(*args)

    def record_parsing(*args):
        parsed_threads.append(threading.get_ident())
        return parser(*args)

    planned = deepcopy(PLANNED)
    planned["outages"][0]["coordinates"] = {"lat": 47, "lng": 19}
    # Represent a bulk national feed with only one matching location.
    for i in range(500):
        record = deepcopy(planned["outages"][0])
        record["id"] = f"unrelated-{i}"
        record["addressRanges"][0]["city"] = "Másikváros"
        record["coordinates"] = {"lat": 48, "lng": 20}
        planned["outages"].append(record)
    with (
        # HA's test fixture executes executor jobs inline; use a real worker here.
        patch.object(hass, "async_add_executor_job", side_effect=asyncio.to_thread),
        patch.object(coordinator_module, "match_snapshot", side_effect=record_matching) as matching,
        patch.object(eon, "parse_response", side_effect=record_parsing),
    ):
        entry = await setup(hass, aioclient_mock, planned=planned, location=LOCATION)
        coordinator = entry.runtime_data
        assert matching.call_count == 1
        assert len(parsed_threads) == 2
        assert main_thread not in matched_threads + parsed_threads
        # A manual refresh inside the network cooldown returns the same snapshot.
        await coordinator.async_refresh()
        assert matching.call_count == 1
        assert aioclient_mock.call_count == 2

    with (
        patch.object(coordinator_module, "match_outage", side_effect=AssertionError("rescanned")),
        patch.object(coordinator_module, "match_range", side_effect=AssertionError("rescanned")),
        patch.object(coordinator_module, "city_matches", side_effect=AssertionError("rescanned")),
        patch.object(Point, "distance", side_effect=AssertionError("recomputed distance")),
    ):
        for now in [
            NOW + timedelta(minutes=1),
            NOW + timedelta(days=1),
            NOW + timedelta(days=1, hours=6),
        ]:
            coordinator._tick(now)
            await hass.async_block_till_done()
            assert coordinator.usable("planned")
            assert coordinator.nearby_usable("planned")
            coordinator.faults()
            coordinator.nearby_faults()
            coordinator.records_without_coordinates("planned")
            coordinator.records_without_coordinates("faults")
        # The interval has ended; neither matched nor nearby upcoming counts retain it.
        for key in ["planned_count", "nearby_planned_count"]:
            assert hass.states.get(entity_id(hass, entry, "sensor", key)).state == "0"
        assert (
            hass.states.get(entity_id(hass, entry, "binary_sensor", "planned_now")).state == "off"
        )
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


async def test_new_snapshot_and_location_invalidate_matches(hass, aioclient_mock):
    entry = await setup(hass, aioclient_mock, location=LOCATION)
    coordinator = entry.runtime_data
    original = coordinator._matches
    with patch.object(coordinator.client, "async_fetch", return_value=coordinator.data):
        await coordinator.async_refresh()
        assert coordinator._matches is original
        coordinator.address = Address("Másikváros")
        await coordinator.async_refresh()
        assert coordinator.matched_planned() == []
        assert coordinator.faults() == []
        coordinator.search_area = SearchArea(Point(48, 20), 100)
        await coordinator.async_refresh()
        assert coordinator.nearby_planned() == []

    # A genuinely new download replaces matches rather than accumulating old ones.
    aioclient_mock.clear_requests()
    empty = {"generatedAt": PLANNED["generatedAt"], "outages": []}
    aioclient_mock.get(eon.PLANNED_URL, json=empty)
    aioclient_mock.get(eon.FAULTS_URL, json={**FAULTS, "outages": []})
    with patch.object(eon, "monotonic", return_value=10**12):
        await coordinator.async_refresh()
    assert coordinator.matched_planned(include_cancelled=True) == []
    assert coordinator.nearby_planned(include_past=True) == []
    assert coordinator.faults() == []
