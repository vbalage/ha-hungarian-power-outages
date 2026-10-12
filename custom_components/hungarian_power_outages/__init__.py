"""Hungarian Power Outages integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_PROVIDER, DOMAIN, PROVIDER
from .coordinator import OutageCoordinator
from .providers import PROVIDERS

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON, Platform.CALENDAR]
type OutageConfigEntry = ConfigEntry[OutageCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: OutageConfigEntry) -> bool:
    clients = hass.data.setdefault(DOMAIN, {})
    provider = entry.data.get(CONF_PROVIDER, PROVIDER)
    if provider not in clients:
        clients[provider] = PROVIDERS[provider](async_get_clientsession(hass))
    coordinator = OutageCoordinator(hass, entry, clients[provider])
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(coordinator.start_clock())
    return True


async def async_unload_entry(hass: HomeAssistant, entry: OutageConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        # The session belongs to HA; discard our cache only when its last entry unloads.
        provider = entry.data.get(CONF_PROVIDER, PROVIDER)
        others = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != entry.entry_id
            and hasattr(e, "runtime_data")
            and e.data.get(CONF_PROVIDER, PROVIDER) == provider
        ]
        if not others:
            hass.data.get(DOMAIN, {}).pop(provider, None)
    return unloaded
