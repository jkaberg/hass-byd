"""Constants for the BYD Vehicle integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "byd_vehicle"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.DEVICE_TRACKER,
    Platform.LOCK,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONF_BASE_URL = "base_url"
CONF_COUNTRY_CODE = "country_code"
CONF_LANGUAGE = "language"
CONF_POLL_INTERVAL = "poll_interval"
CONF_GPS_POLL_INTERVAL = "gps_poll_interval"
CONF_SMART_GPS_POLLING = "smart_gps_polling"
CONF_GPS_ACTIVE_INTERVAL = "gps_active_interval"
CONF_GPS_INACTIVE_INTERVAL = "gps_inactive_interval"
CONF_DEVICE_PROFILE = "device_profile"
CONF_CONTROL_PIN = "control_pin"
CONF_CLIMATE_DURATION = "climate_duration"

DEFAULT_POLL_INTERVAL = 300
DEFAULT_GPS_POLL_INTERVAL = 300
DEFAULT_SMART_GPS_POLLING = False
DEFAULT_GPS_ACTIVE_INTERVAL = 30
DEFAULT_GPS_INACTIVE_INTERVAL = 600
DEFAULT_CLIMATE_DURATION = 1
DEFAULT_COUNTRY = "Netherlands"
DEFAULT_COUNTRY_CODE = "NL"
DEFAULT_LANGUAGE = "en"

BASE_URLS: dict[str, str] = {
    "Europe": "https://dilinkappoversea-eu.byd.auto",
    "Australia": "https://dilinkappoversea-au.byd.auto",
}

COUNTRY_OPTIONS: dict[str, tuple[str, str]] = {
    "Australia": ("AU", "en"),
    "Austria": ("AT", "de"),
    "Belgium": ("BE", "en"),
    "Brazil": ("BR", "pt"),
    "Colombia": ("CO", "es"),
    "Costa Rica": ("CR", "es"),
    "Denmark": ("DK", "da"),
    "El Salvador": ("SV", "es"),
    "France": ("FR", "fr"),
    "Germany": ("DE", "de"),
    "Hong Kong": ("HK", "zh"),
    "Hungary": ("HU", "hu"),
    "India": ("IN", "en"),
    "Indonesia": ("ID", "id"),
    "Italy": ("IT", "it"),
    "Japan": ("JP", "ja"),
    "Malaysia": ("MY", "ms"),
    "Mexico": ("MX", "es"),
    "Netherlands": ("NL", "nl"),
    "New Zealand": ("NZ", "en"),
    "Norway": ("NO", "no"),
    "Pakistan": ("PK", "en"),
    "Philippines": ("PH", "en"),
    "Poland": ("PL", "pl"),
    "South Africa": ("ZA", "en"),
    "South Korea": ("KR", "ko"),
    "Sweden": ("SE", "sv"),
    "Thailand": ("TH", "th"),
    "Turkey": ("TR", "tr"),
    "United Kingdom": ("GB", "en"),
    "Uzbekistan": ("UZ", "uz"),
}
