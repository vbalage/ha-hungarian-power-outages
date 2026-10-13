"""Malformed feeds and requests must not become false all-clear results."""

from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from custom_components.hungarian_power_outages.models import FaultReport
from custom_components.hungarian_power_outages.providers.eon import (
    FAULTS_URL,
    PLANNED_URL,
    EonClient,
    FeedError,
    parse_feed,
)

NOW = datetime(2026, 10, 12, 8, 30, tzinfo=UTC)
PLANNED = {
    "generatedAt": {"date": "2026-10-12", "time": "10:00:00"},
    "outages": [
        {
            "id": "test-planned",
            "status": "PENDING",
            "addressRanges": [
                {
                    "city": "Mintaváros",
                    "zipCode": "9999",
                    "street": "Példa utca",
                    "startNum": "2",
                    "endNum": "20",
                }
            ],
            "intervals": [
                {
                    "from": {"date": "2026-10-13", "time": "08:00:00"},
                    "to": {"date": "2026-10-13", "time": "16:00:00"},
                    "isCancelled": False,
                }
            ],
        }
    ],
}
FAULTS = {
    "generatedAt": {"date": "2026-10-12", "time": "10:20:00"},
    "outages": [
        {
            "id": "test-fault",
            "city": "Mintaváros",
            "from": {"date": "2026-10-12", "time": "09:00:00"},
            "consumers": 10,
            "coordinates": {"lat": 0, "lng": 0},
        }
    ],
}


def test_planned_timezone_and_cancellations():
    payload = deepcopy(PLANNED)
    payload["outages"][0]["intervals"][0]["isCancelled"] = True
    feed = parse_feed(payload, "planned", NOW)
    assert feed.items[0].start.utcoffset().total_seconds() == 7200
    assert feed.items[0].cancelled and feed.usable(NOW, 48)


def test_valid_empty_is_different_from_invalid():
    assert (
        parse_feed({"outages": [], "generatedAt": PLANNED["generatedAt"]}, "planned", NOW).items
        == ()
    )
    with pytest.raises(FeedError):
        parse_feed({"unexpected": []}, "planned", NOW)


@pytest.mark.parametrize("change", ["missing_city", "bad_interval", "bad_boolean", "future_feed"])
def test_malformed_planned_fails_whole_feed(change):
    payload = deepcopy(PLANNED)
    record = payload["outages"][0]
    if change == "missing_city":
        del record["addressRanges"][0]["city"]
    if change == "bad_interval":
        record["intervals"][0]["to"] = {"date": "2026-10-12", "time": "08:00:00"}
    if change == "bad_boolean":
        record["intervals"][0]["isCancelled"] = "false"
    if change == "future_feed":
        payload["generatedAt"]["date"] = "2027-01-01"
    with pytest.raises(FeedError):
        parse_feed(payload, "planned", NOW)


def test_fault_keeps_old_and_zero_consumer_reports():
    payload = deepcopy(FAULTS)
    payload["outages"][0]["consumers"] = 0
    payload["outages"][0]["from"]["date"] = "2026-01-01"
    item = parse_feed(payload, "faults", NOW).items[0]
    assert isinstance(item, FaultReport) and item.consumers == 0 and item.start.year == 2026


async def test_requests_shared_across_addresses(aioclient_mock, hass):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    aioclient_mock.get(PLANNED_URL, json=PLANNED)
    aioclient_mock.get(FAULTS_URL, json=FAULTS)
    client = EonClient(async_get_clientsession(hass))
    with patch(
        "custom_components.hungarian_power_outages.providers.eon.datetime", wraps=datetime
    ) as clock:
        clock.now.return_value = NOW
        first = await client.async_fetch()
        second = await client.async_fetch()
    assert first is second and aioclient_mock.call_count == 2 and first.planned.error is None


async def test_feed_failure_is_independent(aioclient_mock, hass):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    aioclient_mock.get(PLANNED_URL, status=503)
    aioclient_mock.get(FAULTS_URL, json=FAULTS)
    client = EonClient(async_get_clientsession(hass))
    with patch(
        "custom_components.hungarian_power_outages.providers.eon.datetime", wraps=datetime
    ) as clock:
        clock.now.return_value = NOW
        snapshot = await client.async_fetch()
    assert (
        snapshot.planned.error == "cannot_connect"
        and not snapshot.planned.usable(NOW, 48)
        and snapshot.faults.usable(NOW, 48)
    )


@pytest.mark.parametrize(
    "anomaly", ["blank_street", "empty_addresses", "zero_interval", "empty_intervals"]
)
def test_known_source_anomalies_are_explicit(anomaly):
    payload = deepcopy(PLANNED)
    record = payload["outages"][0]
    if anomaly == "blank_street":
        record["addressRanges"][0]["street"] = ""
    elif anomaly == "empty_addresses":
        record["addressRanges"] = []
    elif anomaly == "zero_interval":
        record["intervals"][0]["to"] = record["intervals"][0]["from"]
    else:
        record["intervals"] = []
    feed = parse_feed(payload, "planned", NOW)
    assert feed.error is None
    if anomaly in ("blank_street", "empty_addresses"):
        assert len(feed.items) == 1
        assert feed.addressless_records == (anomaly == "empty_addresses")
    else:
        assert not feed.items and feed.skipped_records == 1
        assert bool(feed.incomplete_addresses) == (anomaly != "empty_addresses")


async def test_valid_feed_larger_than_old_limit(aioclient_mock, hass):
    """A growing national feed must not be mistaken for malformed data."""
    import json

    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from custom_components.hungarian_power_outages.models import Address, match_outage

    raw = json.dumps(PLANNED).encode() + b" " * (10 * 1024 * 1024)
    aioclient_mock.get(PLANNED_URL, content=raw)
    aioclient_mock.get(FAULTS_URL, json=FAULTS)
    with patch(
        "custom_components.hungarian_power_outages.providers.eon.datetime", wraps=datetime
    ) as clock:
        clock.now.return_value = NOW
        snapshot = await EonClient(async_get_clientsession(hass)).async_fetch()
    assert snapshot.planned.error is None
    assert snapshot.planned.usable(NOW, 48)
    assert len(snapshot.planned.items) == 1
    assert not any(match_outage(Address("Pákozd"), item) for item in snapshot.planned.items)


async def test_oversized_feed_preserves_previous_data(aioclient_mock, hass):
    """Enforce the actual byte limit and retain the last successful snapshot."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    aioclient_mock.get(PLANNED_URL, content=b" " * 257, headers={"Content-Length": "1"})
    previous = parse_feed(PLANNED, "planned", NOW)
    with patch("custom_components.hungarian_power_outages.providers.eon.MAX_BYTES", 256):
        feed = await EonClient(async_get_clientsession(hass))._fetch(
            "planned", PLANNED_URL, previous
        )
    assert feed.error == "feed_too_large"
    assert feed.items == previous.items
    assert feed.fetched_at == previous.fetched_at
    assert not feed.usable(NOW, 48)
