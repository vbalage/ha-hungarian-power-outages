"""Shared entity identity and availability."""

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SOURCE_URL
from .coordinator import OutageCoordinator


class OutageEntity(CoordinatorEntity[OutageCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: OutageCoordinator, key: str, feed: str | None = None) -> None:
        super().__init__(coordinator)
        self.feed_kind = feed
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="E.ON Hungary",
            configuration_url=SOURCE_URL,
        )

    @property
    def available(self) -> bool:
        return super().available and (
            self.feed_kind is None or self.coordinator.usable(self.feed_kind)
        )
