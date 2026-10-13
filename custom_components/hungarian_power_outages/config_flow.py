"""UI setup, address reconfiguration and polling options."""

import math
from hashlib import sha256

import probatio as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_CITY,
    CONF_HOUSE_NUMBER,
    CONF_LOCATION,
    CONF_PARCEL_NUMBER,
    CONF_POLL_HOURS,
    CONF_POSTCODE,
    CONF_PROVIDER,
    CONF_STALE_HOURS,
    CONF_STREET,
    DEFAULT_POLL_HOURS,
    DEFAULT_RADIUS_METERS,
    DEFAULT_STALE_HOURS,
    DOMAIN,
    MAX_RADIUS_METERS,
    PROVIDER,
)
from .models import Address, parcel_number
from .providers import PROVIDERS


def address_schema(defaults: dict) -> vol.Schema:
    schema = {
        vol.Required(
            CONF_PROVIDER, default=defaults.get(CONF_PROVIDER, PROVIDER)
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(options=list(PROVIDERS), translation_key="provider")
        ),
        vol.Required(CONF_CITY, default=defaults.get(CONF_CITY, "")): str,
        vol.Optional(CONF_POSTCODE, default=defaults.get(CONF_POSTCODE, "")): str,
        vol.Optional(CONF_STREET, default=defaults.get(CONF_STREET, "")): str,
        vol.Optional(CONF_HOUSE_NUMBER, default=defaults.get(CONF_HOUSE_NUMBER, "")): str,
        vol.Optional(CONF_PARCEL_NUMBER, default=defaults.get(CONF_PARCEL_NUMBER, "")): str,
    }
    schema[vol.Optional("nearby_enabled", default=bool(defaults.get(CONF_LOCATION)))] = bool
    location_key = (
        vol.Optional(CONF_LOCATION, default=defaults[CONF_LOCATION])
        if defaults.get(CONF_LOCATION)
        else vol.Optional(CONF_LOCATION)
    )
    schema[location_key] = selector.LocationSelector(selector.LocationSelectorConfig(radius=True))
    return vol.Schema(schema)


def cleaned(data: dict) -> dict:
    result = {
        key: str(data.get(key, "")).strip()
        for key in (CONF_PROVIDER, CONF_CITY, CONF_POSTCODE, CONF_STREET, CONF_HOUSE_NUMBER)
    }
    if parcel := str(data.get(CONF_PARCEL_NUMBER, "")).strip():
        result[CONF_PARCEL_NUMBER] = parcel_number(parcel) or parcel
    result["nearby_enabled"] = data.get("nearby_enabled", bool(data.get(CONF_LOCATION)))
    if result["nearby_enabled"] and data.get(CONF_LOCATION):
        location = dict(data[CONF_LOCATION])
        location.setdefault("radius", DEFAULT_RADIUS_METERS)
        result[CONF_LOCATION] = location
    return result


def invalid(data: dict) -> bool:
    location = data.get(CONF_LOCATION)
    if data.get("nearby_enabled") and not location:
        return True
    if location:
        lat, lon, radius = (location.get(key) for key in ("latitude", "longitude", "radius"))
        if not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            for v in (lat, lon, radius)
        ):
            return True
        if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 < radius <= MAX_RADIUS_METERS):
            return True
    return (
        not data[CONF_CITY]
        or bool(data.get(CONF_PARCEL_NUMBER) and not parcel_number(data[CONF_PARCEL_NUMBER]))
        or bool(data[CONF_HOUSE_NUMBER] and not data[CONF_STREET])
        or bool(
            data[CONF_POSTCODE]
            and (len(data[CONF_POSTCODE]) != 4 or not data[CONF_POSTCODE].isdigit())
        )
    )


def unique_id(data: dict) -> str:
    address = Address(
        *(
            data.get(key, "")
            for key in (
                CONF_CITY,
                CONF_POSTCODE,
                CONF_STREET,
                CONF_HOUSE_NUMBER,
                CONF_PARCEL_NUMBER,
            )
        )
    )
    return data[CONF_PROVIDER] + "_" + sha256(address.identity.encode()).hexdigest()


def title(data: dict) -> str:
    return "E.ON · " + ", ".join(
        x
        for x in (
            data[CONF_CITY],
            " ".join(x for x in (data[CONF_STREET], data[CONF_HOUSE_NUMBER]) if x),
            "HRSZ " + data[CONF_PARCEL_NUMBER] if data.get(CONF_PARCEL_NUMBER) else "",
        )
        if x
    )


class OutageConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OutageOptionsFlow()

    async def _validate_feed(self, data: dict) -> str | None:
        client = self.hass.data.get(DOMAIN, {}).get(data[CONF_PROVIDER]) or PROVIDERS[
            data[CONF_PROVIDER]
        ](async_get_clientsession(self.hass))
        snapshot = await client.async_fetch()
        # Permit setup if either feed works. Health entities explain partial failures.
        if snapshot.planned.error and snapshot.faults.error:
            errors = (snapshot.planned.error, snapshot.faults.error)
            if "feed_too_large" in errors:
                return "feed_too_large"
            return "invalid_feed" if "invalid_feed" in errors else "cannot_connect"
        return None

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            data = cleaned(user_input)
            if invalid(data):
                errors["base"] = "invalid_address"
            elif error := await self._validate_feed(data):
                errors["base"] = error
            else:
                await self.async_set_unique_id(unique_id(data))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=title(data), data=data)
        return self.async_show_form(
            step_id="user", data_schema=address_schema(user_input or {}), errors=errors
        )

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            data = cleaned(user_input)
            if invalid(data):
                errors["base"] = "invalid_address"
            elif error := await self._validate_feed(data):
                errors["base"] = error
            else:
                identifier = unique_id(data)
                if any(
                    e.entry_id != entry.entry_id and e.unique_id == identifier
                    for e in self.hass.config_entries.async_entries(DOMAIN)
                ):
                    return self.async_abort(reason="already_configured")
                self.hass.config_entries.async_update_entry(
                    entry, unique_id=identifier, title=title(data)
                )
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=address_schema(user_input or entry.data),
            errors=errors,
        )


class OutageOptionsFlow(config_entries.OptionsFlowWithReload):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_HOURS,
                        default=self.config_entry.options.get(CONF_POLL_HOURS, DEFAULT_POLL_HOURS),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=168)),
                    vol.Required(
                        CONF_STALE_HOURS,
                        default=self.config_entry.options.get(
                            CONF_STALE_HOURS, DEFAULT_STALE_HOURS
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=168)),
                }
            ),
        )
