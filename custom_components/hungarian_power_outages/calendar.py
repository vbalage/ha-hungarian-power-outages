"""Read-only calendar of matching planned outages."""

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .entity import OutageEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [OutageCalendar(coordinator)]
        + ([NearbyCalendar(coordinator)] if coordinator.search_area else [])
    )


class OutageCalendar(OutageEntity, CalendarEntity):
    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "planned_calendar", "planned")

    def _event(self, outage, level) -> CalendarEvent:
        return CalendarEvent(
            start=outage.start,
            end=outage.end,
            summary="E.ON planned power outage",
            description=f"Match: {level}. Reported by E.ON; electricity supply is not measured.",
            uid=f"{outage.id}_{outage.start.isoformat()}",
        )

    @property
    def event(self) -> CalendarEvent | None:
        planned = self.coordinator.relevant_planned()
        return self._event(*planned[0]) if planned else None

    async def async_get_events(self, hass, start_date, end_date) -> list[CalendarEvent]:
        if not self.available:
            raise HomeAssistantError("Planned outage feed is unavailable or stale")
        start, end = dt_util.as_utc(start_date), dt_util.as_utc(end_date)
        return [
            self._event(item, level)
            for item, level in self.coordinator.matched_planned()
            if item.start < end and item.end > start
        ]


class NearbyCalendar(OutageEntity, CalendarEntity):
    """Calendar of geographic candidates; no claim that an address is covered."""

    def __init__(self, coordinator):
        super().__init__(coordinator, "nearby_planned_calendar")

    @property
    def available(self):
        return super().available and self.coordinator.nearby_usable("planned")

    def _event(self, item, distance):
        return CalendarEvent(
            start=item.start,
            end=item.end,
            summary="Nearby E.ON planned outage",
            description=f"Provider marker is {round(distance)} m from your chosen location. Possibly relevant; household coverage is not confirmed.",
            uid=f"nearby_{item.id}_{item.start.isoformat()}",
        )

    @property
    def event(self):
        reports = self.coordinator.nearby_planned()
        return self._event(*reports[0]) if reports else None

    async def async_get_events(self, hass, start_date, end_date):
        if not self.available:
            raise HomeAssistantError("Nearby planned outage feed is unavailable or stale")
        start, end = dt_util.as_utc(start_date), dt_util.as_utc(end_date)
        return [
            self._event(item, distance)
            for item, distance in self.coordinator.nearby_planned(include_past=True)
            if item.start < end and item.end > start
        ]
