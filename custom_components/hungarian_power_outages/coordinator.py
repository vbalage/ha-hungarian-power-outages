"""Polling plus local time updates; outage intervals advance without network calls."""

import logging
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CITY,
    CONF_HOUSE_NUMBER,
    CONF_LOCATION,
    CONF_PARCEL_NUMBER,
    CONF_POLL_HOURS,
    CONF_POSTCODE,
    CONF_STALE_HOURS,
    CONF_STREET,
    DEFAULT_POLL_HOURS,
    DEFAULT_STALE_HOURS,
    DOMAIN,
)
from .models import (
    Address,
    FaultReport,
    Feed,
    MatchLevel,
    PlannedOutage,
    Point,
    SearchArea,
    Snapshot,
    city_matches,
    match_outage,
    match_range,
)
from .providers import ProviderClient

_LOGGER = logging.getLogger(__name__)


class OutageCoordinator(DataUpdateCoordinator[Snapshot]):
    def __init__(self, hass: HomeAssistant, entry, client: ProviderClient) -> None:
        self.client = client
        self.address = Address(
            *(
                entry.data.get(key, "")
                for key in (
                    CONF_CITY,
                    CONF_POSTCODE,
                    CONF_STREET,
                    CONF_HOUSE_NUMBER,
                    CONF_PARCEL_NUMBER,
                )
            )
        )
        location = entry.data.get(CONF_LOCATION)
        self.search_area = (
            SearchArea(Point(location["latitude"], location["longitude"]), location["radius"])
            if location
            else None
        )
        self.poll_hours = entry.options.get(CONF_POLL_HOURS, DEFAULT_POLL_HOURS)
        self.stale_hours = entry.options.get(CONF_STALE_HOURS, DEFAULT_STALE_HOURS)
        self.now = dt_util.utcnow()
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(hours=self.poll_hours),
        )

    async def _async_update_data(self) -> Snapshot:
        snapshot = await self.client.async_fetch()
        self.now = dt_util.utcnow()
        # Retry a failed feed earlier, while preserving the independent healthy feed.
        failed = snapshot.planned.error or snapshot.faults.error
        self.update_interval = timedelta(minutes=15) if failed else timedelta(hours=self.poll_hours)
        return snapshot

    @callback
    def start_clock(self):
        return async_track_time_interval(self.hass, self._tick, timedelta(minutes=1))

    @callback
    def _tick(self, now: datetime) -> None:
        self.now = dt_util.as_utc(now)
        self.async_update_listeners()

    def usable(self, kind: str) -> bool:
        if not self.data or not self.feed(kind).usable(self.now, self.stale_hours):
            return False
        if kind == "planned" and any(
            match_range(self.address, item) for item in self.data.planned.incomplete_addresses
        ):
            return False
        return True

    def feed(self, kind: str) -> Feed:
        return self.data.planned if kind == "planned" else self.data.faults

    def matched_planned(
        self, *, include_cancelled: bool = False
    ) -> list[tuple[PlannedOutage, MatchLevel]]:
        result = []
        for item in self.data.planned.items:
            if isinstance(item, PlannedOutage) and (include_cancelled or not item.cancelled):
                level = match_outage(self.address, item)
                if level:
                    result.append((item, level))
        return sorted(result, key=lambda pair: pair[0].start)

    def relevant_planned(self) -> list[tuple[PlannedOutage, MatchLevel]]:
        return [(item, level) for item, level in self.matched_planned() if item.end > self.now]

    def faults(self) -> list[FaultReport]:
        return sorted(
            (
                item
                for item in self.data.faults.items
                if isinstance(item, FaultReport) and city_matches(self.address.city, item.city)
            ),
            key=lambda item: item.start,
            reverse=True,
        )

    def nearby_usable(self, kind: str) -> bool:
        if (
            not self.search_area
            or not self.data
            or not self.feed(kind).usable(self.now, self.stale_hours)
        ):
            return False
        return kind != "planned" or not any(
            self.search_area.includes(point) for point in self.data.planned.incomplete_coordinates
        )

    def nearby_planned(self, *, include_past: bool = False) -> list[tuple[PlannedOutage, float]]:
        if not self.search_area:
            return []
        return sorted(
            [
                (item, self.search_area.center.distance(item.coordinates))
                for item in self.data.planned.items
                if isinstance(item, PlannedOutage)
                and not item.cancelled
                and (include_past or item.end > self.now)
                and self.search_area.includes(item.coordinates)
            ],
            key=lambda pair: (pair[0].start, pair[1]),
        )

    def nearby_faults(self) -> list[tuple[FaultReport, float, str]]:
        if not self.search_area:
            return []
        result = []
        for item in self.data.faults.items:
            if isinstance(item, FaultReport) and self.search_area.includes(
                item.coordinates, item.area_radius
            ):
                distance = self.search_area.center.distance(item.coordinates)
                basis = "marker" if distance <= self.search_area.radius else "provider_area_overlap"
                result.append((item, distance, basis))
        return sorted(result, key=lambda row: row[1])
