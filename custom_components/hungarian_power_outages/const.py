"""Integration constants."""

DOMAIN = "hungarian_power_outages"
NAME = "Hungarian Power Outages"
PROVIDER = "eon"
SOURCE_URL = "https://www.eon.hu/hu/lakossagi/aram/aramszunet-informaciok.html"
DEFAULT_POLL_HOURS = 24
DEFAULT_STALE_HOURS = 48
MIN_REFRESH_SECONDS = 60
CONF_PROVIDER = "provider"
CONF_CITY = "city"
CONF_POSTCODE = "postcode"
CONF_STREET = "street"
CONF_HOUSE_NUMBER = "house_number"
CONF_PARCEL_NUMBER = "parcel_number"
CONF_POLL_HOURS = "poll_hours"
CONF_STALE_HOURS = "stale_hours"
CONF_LOCATION = "location"
DEFAULT_RADIUS_METERS = 1000
MAX_RADIUS_METERS = 100000
