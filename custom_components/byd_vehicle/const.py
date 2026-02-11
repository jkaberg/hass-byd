"""Constants for the BYD Vehicle integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "byd_vehicle"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.CLIMATE,
    Platform.LOCK,
    Platform.SWITCH,
]

CONF_BASE_URL = "base_url"
CONF_COUNTRY_CODE = "country_code"
CONF_LANGUAGE = "language"
CONF_POLL_INTERVAL = "poll_interval"
CONF_GPS_POLL_INTERVAL = "gps_poll_interval"

DEFAULT_POLL_INTERVAL = 300
DEFAULT_GPS_POLL_INTERVAL = 300
DEFAULT_COUNTRY_CODE = "NL"
DEFAULT_LANGUAGE = "en"

BASE_URLS: dict[str, str] = {
    "Europe": "https://dilinkappoversea-eu.byd.auto",
    "Australia": "https://dilinkappoversea-au.byd.auto",
}

COUNTRY_LANGUAGES: dict[str, str] = {
    "AU": "en",
    "DE": "de",
    "ES": "es",
    "FR": "fr",
    "IT": "it",
    "NL": "nl",
    "NO": "no",
    "SE": "sv",
    "DK": "da",
    "FI": "fi",
}
