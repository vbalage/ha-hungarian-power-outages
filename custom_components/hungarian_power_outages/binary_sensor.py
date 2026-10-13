"""A planned outage interval matches the configured location now."""

from homeassistant.components.binary_sensor import BinarySensorEntity

from .entity import OutageEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([PlannedNowSensor(entry.runtime_data)])


class PlannedNowSensor(OutageEntity, BinarySensorEntity):
    _attr_icon = "mdi:transmission-tower-off"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "planned_now", "planned")

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.active_planned())

    @property
    def extra_state_attributes(self):
        return {
            "matches": [
                {"id": item.id, "match": str(level)}
                for item, level in self.coordinator.active_planned()
            ],
            "meaning": "scheduled_interval_matches_location; electricity_supply_not_measured",
        }
