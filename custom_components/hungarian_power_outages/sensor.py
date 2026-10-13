"""Outage summaries and feed health sensors."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory

from .entity import OutageEntity


def marker_coordinates(item):
    """Expose usable public provider markers for companion cards."""
    return (
        {"latitude": item.coordinates.latitude, "longitude": item.coordinates.longitude}
        if item.coordinates
        else {}
    )


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            SummarySensor(coordinator, key, kind)
            for key, kind in (
                ("planned_count", "planned"),
                ("next_planned_start", "planned"),
                ("next_planned_end", "planned"),
                ("fault_reports", "faults"),
            )
        ]
        + [
            FeedSensor(coordinator, kind, field)
            for kind in ("planned", "faults")
            for field in ("health", "last_success", "generated_at")
        ]
        + (
            [
                NearbySensor(coordinator, "nearby_planned_count", "planned"),
                NearbySensor(coordinator, "nearby_fault_reports", "faults"),
            ]
            if coordinator.search_area
            else []
        )
    )


class SummarySensor(OutageEntity, SensorEntity):
    def __init__(self, coordinator, key, kind) -> None:
        super().__init__(coordinator, key, kind)
        self.key = key
        if key.startswith("next_"):
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        else:
            self._attr_native_unit_of_measurement = (
                "reports" if key == "fault_reports" else "outages"
            )

    @property
    def native_value(self):
        if self.key == "fault_reports":
            return len(self.coordinator.faults())
        planned = self.coordinator.relevant_planned()
        if self.key == "planned_count":
            return len(planned)
        if not planned:
            return None
        return planned[0][0].start if self.key == "next_planned_start" else planned[0][0].end

    @property
    def extra_state_attributes(self):
        if self.key == "fault_reports":
            reports = self.coordinator.faults()
            return {
                "coverage": "area",
                "total_reports": len(reports),
                "reports": [
                    {
                        "id": r.id,
                        **marker_coordinates(r),
                        "provider_radius_m": r.area_radius,
                        "started_at": r.start.isoformat(),
                        "affected_customers": r.consumers,
                    }
                    for r in reports[:25]
                ],
                "truncated": len(reports) > 25,
                "household_coverage_confirmed": False,
            }
        planned = self.coordinator.relevant_planned()
        return {
            "source_skipped_records": self.coordinator.data.planned.skipped_records,
            "source_addressless_records": self.coordinator.data.planned.addressless_records,
            "outages": [
                {
                    "id": r.id,
                    **marker_coordinates(r),
                    "start": r.start.isoformat(),
                    "end": r.end.isoformat(),
                    "match": str(level),
                }
                for r, level in planned[:25]
            ],
            "truncated": len(planned) > 25,
        }


class FeedSensor(OutageEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, kind, field) -> None:
        super().__init__(coordinator, f"{kind}_{field}")
        self.kind, self.field = kind, field
        if field == "health":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = ["ok", "stale", "cannot_connect", "invalid_feed", "degraded"]
        else:
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        feed = self.coordinator.feed(self.kind)
        if self.field == "health":
            if feed.error:
                return feed.error
            if not feed.usable(self.coordinator.now, self.coordinator.stale_hours):
                return "stale"
            return "degraded" if feed.skipped_records or feed.addressless_records else "ok"
        return feed.fetched_at if self.field == "last_success" else feed.generated_at


class NearbySensor(OutageEntity, SensorEntity):
    """Geographic candidates, kept distinct from address-matched entities."""

    def __init__(self, coordinator, key, kind):
        super().__init__(coordinator, key)
        self.kind = kind
        self._attr_native_unit_of_measurement = "reports" if kind == "faults" else "outages"

    @property
    def available(self):
        return super().available and self.coordinator.nearby_usable(self.kind)

    @property
    def native_value(self):
        return len(
            self.coordinator.nearby_planned()
            if self.kind == "planned"
            else self.coordinator.nearby_faults()
        )

    @property
    def extra_state_attributes(self):
        if self.kind == "planned":
            rows = self.coordinator.nearby_planned()
            reports = [
                {
                    "id": item.id,
                    **marker_coordinates(item),
                    "start": item.start.isoformat(),
                    "end": item.end.isoformat(),
                    "distance_m": round(distance),
                    "match": "nearby",
                    "address_information_available": bool(item.addresses),
                }
                for item, distance in rows[:25]
            ]
        else:
            rows = self.coordinator.nearby_faults()
            reports = [
                {
                    "id": item.id,
                    **marker_coordinates(item),
                    "started_at": item.start.isoformat(),
                    "distance_m": round(distance),
                    "match": "nearby",
                    "basis": basis,
                    "provider_radius_m": item.area_radius,
                    "affected_customers": item.consumers,
                }
                for item, distance, basis in rows[:25]
            ]
        return {
            "reports": reports,
            "truncated": len(rows) > 25,
            "household_coverage_confirmed": False,
            "radius_m": self.coordinator.search_area.radius,
            "search_center": {
                "latitude": self.coordinator.search_area.center.latitude,
                "longitude": self.coordinator.search_area.center.longitude,
            },
            "source_records_without_coordinates": self.coordinator.records_without_coordinates(
                self.kind
            ),
        }
