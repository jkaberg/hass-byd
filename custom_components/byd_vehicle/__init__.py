"""BYD Vehicle integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_DEVICE_PROFILE,
    CONF_GPS_ACTIVE_INTERVAL,
    CONF_GPS_INACTIVE_INTERVAL,
    CONF_GPS_POLL_INTERVAL,
    CONF_POLL_INTERVAL,
    CONF_SMART_GPS_POLLING,
    DEFAULT_GPS_ACTIVE_INTERVAL,
    DEFAULT_GPS_INACTIVE_INTERVAL,
    DEFAULT_GPS_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_SMART_GPS_POLLING,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import BydApi, BydDataUpdateCoordinator, BydGpsUpdateCoordinator
from .device_fingerprint import generate_device_profile


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up BYD Vehicle from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Ensure a device fingerprint exists (backfill for pre-existing entries)
    if CONF_DEVICE_PROFILE not in entry.data:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_DEVICE_PROFILE: generate_device_profile()}
        )

    session = async_get_clientsession(hass)
    api = BydApi(hass, entry, session)

    poll_interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
    gps_interval = entry.options.get(CONF_GPS_POLL_INTERVAL, DEFAULT_GPS_POLL_INTERVAL)
    smart_gps = entry.options.get(CONF_SMART_GPS_POLLING, DEFAULT_SMART_GPS_POLLING)
    gps_active = entry.options.get(
        CONF_GPS_ACTIVE_INTERVAL, DEFAULT_GPS_ACTIVE_INTERVAL
    )
    gps_inactive = entry.options.get(
        CONF_GPS_INACTIVE_INTERVAL, DEFAULT_GPS_INACTIVE_INTERVAL
    )

    coordinator = BydDataUpdateCoordinator(hass, api, poll_interval)
    gps_coordinator = BydGpsUpdateCoordinator(
        hass,
        api,
        gps_interval,
        telemetry_coordinator=coordinator,
        smart_polling=smart_gps,
        active_interval=gps_active,
        inactive_interval=gps_inactive,
    )

    try:
        await coordinator.async_config_entry_first_refresh()
        await gps_coordinator.async_config_entry_first_refresh()
    except Exception as exc:  # noqa: BLE001
        raise ConfigEntryNotReady from exc

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "gps_coordinator": gps_coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if entry_data and "api" in entry_data:
            await entry_data["api"]._invalidate_client()
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
