"""Polling plus local time updates; outage intervals advance without network calls."""

import logging
from dataclasses import dataclass
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


@dataclass(frozen=True)
class LocationMatches:
    """Snapshot-derived matches, built only in an executor worker."""

    planned: list[tuple[PlannedOutage, MatchLevel]]
    planned_with_cancelled: list[tuple[PlannedOutage, MatchLevel]]
    faults: list[FaultReport]
    nearby_planned: list[tuple[PlannedOutage, float]]
    nearby_faults: list[tuple[FaultReport, float, str]]
    incomplete_address: bool
    incomplete_area: bool
    planned_without_coordinates: int
    faults_without_coordinates: int


def match_snapshot(
    snapshot: Snapshot, address: Address, area: SearchArea | None
) -> LocationMatches:
    """Perform all full-feed scans once; never call this from entity properties."""
    planned = []
    nearby_planned = []
    faults = []
    nearby_faults = []
    for item in snapshot.planned.items:
        if not isinstance(item, PlannedOutage):
            continue
        if level := match_outage(address, item):
            planned.append((item, level))
        if area and not item.cancelled and item.coordinates:
            distance = area.center.distance(item.coordinates)
            if distance <= area.radius:
                nearby_planned.append((item, distance))
    for item in snapshot.faults.items:
        if not isinstance(item, FaultReport):
            continue
        if city_matches(address.city, item.city):
            faults.append(item)
        if area and item.coordinates:
            distance = area.center.distance(item.coordinates)
            if distance <= area.radius + item.area_radius:
                basis = "marker" if distance <= area.radius else "provider_area_overlap"
                nearby_faults.append((item, distance, basis))
    planned.sort(key=lambda pair: pair[0].start)
    faults.sort(key=lambda item: item.start, reverse=True)
    nearby_planned.sort(key=lambda pair: (pair[0].start, pair[1]))
    nearby_faults.sort(key=lambda row: row[1])
    return LocationMatches(
        [row for row in planned if not row[0].cancelled],
        planned,
        faults,
        nearby_planned,
        nearby_faults,
        any(match_range(address, item) for item in snapshot.planned.incomplete_addresses),
        bool(area)
        and any(area.includes(point) for point in snapshot.planned.incomplete_coordinates),
        sum(item.coordinates is None for item in snapshot.planned.items),
        sum(item.coordinates is None for item in snapshot.faults.items),
    )


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
        self._matched_snapshot: Snapshot | None = None
        self._matched_location = None
        self._matches = LocationMatches([], [], [], [], [], False, False, 0, 0)
        self._relevant = []
        self._active = []
        self._nearby_relevant = []
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(hours=self.poll_hours),
        )

    async def _async_update_data(self) -> Snapshot:
        snapshot = await self.client.async_fetch()
        location = (self.address, self.search_area)
        if snapshot is not self._matched_snapshot or location != self._matched_location:
            matches = await self.hass.async_add_executor_job(
                match_snapshot, snapshot, self.address, self.search_area
            )
            self._matches = matches
            self._matched_snapshot = snapshot
            self._matched_location = location
        self._advance_clock(dt_util.utcnow())
        # Retry a failed feed earlier, while preserving the independent healthy feed.
        failed = snapshot.planned.error or snapshot.faults.error
        self.update_interval = timedelta(minutes=15) if failed else timedelta(hours=self.poll_hours)
        return snapshot

    @callback
    def start_clock(self):
        return async_track_time_interval(self.hass, self._tick, timedelta(minutes=1))

    def _advance_clock(self, now: datetime) -> None:
        """Only compare times in the location's cached candidates, once per update."""
        self.now = dt_util.as_utc(now)
        self._relevant = [row for row in self._matches.planned if row[0].end > self.now]
        self._active = [row for row in self._relevant if row[0].start <= self.now]
        self._nearby_relevant = [
            row for row in self._matches.nearby_planned if row[0].end > self.now
        ]

    @callback
    def _tick(self, now: datetime) -> None:
        self._advance_clock(now)
        self.async_update_listeners()

    def usable(self, kind: str) -> bool:
        return bool(
            self.data
            and self.feed(kind).usable(self.now, self.stale_hours)
            and (kind != "planned" or not self._matches.incomplete_address)
        )

    def feed(self, kind: str) -> Feed:
        return self.data.planned if kind == "planned" else self.data.faults

    def matched_planned(
        self, *, include_cancelled: bool = False
    ) -> list[tuple[PlannedOutage, MatchLevel]]:
        return self._matches.planned_with_cancelled if include_cancelled else self._matches.planned

    def relevant_planned(self) -> list[tuple[PlannedOutage, MatchLevel]]:
        return self._relevant

    def active_planned(self) -> list[tuple[PlannedOutage, MatchLevel]]:
        return self._active

    def faults(self) -> list[FaultReport]:
        return self._matches.faults

    def nearby_usable(self, kind: str) -> bool:
        return bool(
            self.search_area
            and self.data
            and self.feed(kind).usable(self.now, self.stale_hours)
            and (kind != "planned" or not self._matches.incomplete_area)
        )

    def nearby_planned(self, *, include_past: bool = False) -> list[tuple[PlannedOutage, float]]:
        return self._matches.nearby_planned if include_past else self._nearby_relevant

    def nearby_faults(self) -> list[tuple[FaultReport, float, str]]:
        return self._matches.nearby_faults

    def records_without_coordinates(self, kind: str) -> int:
        return (
            self._matches.planned_without_coordinates
            if kind == "planned"
            else self._matches.faults_without_coordinates
        )
