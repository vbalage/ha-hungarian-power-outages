"""Manual refresh; available even if a feed is broken."""

from homeassistant.components.button import ButtonEntity

from .entity import OutageEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([RefreshButton(entry.runtime_data)])


class RefreshButton(OutageEntity, ButtonEntity):
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "refresh")

    async def async_press(self) -> None:
        await self.coordinator.async_refresh()
