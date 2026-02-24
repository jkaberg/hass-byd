"""BYD Vehicle integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pybyd import BydClient

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
    MAX_GPS_ACTIVE_INTERVAL,
    MAX_GPS_INACTIVE_INTERVAL,
    MAX_GPS_POLL_INTERVAL,
    MAX_POLL_INTERVAL,
    MIN_GPS_ACTIVE_INTERVAL,
    MIN_GPS_INACTIVE_INTERVAL,
    MIN_GPS_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
    PLATFORMS,
)
from .coordinator import BydApi, BydDataUpdateCoordinator, BydGpsUpdateCoordinator
from .device_fingerprint import async_generate_device_profile

_LOGGER = logging.getLogger(__name__)


def _sanitize_interval(value: int, default: int, min_value: int, max_value: int) -> int:
    """Clamp interval values so stale options cannot break scheduling."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, parsed))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up BYD Vehicle from a config entry."""
    _LOGGER.debug("Setting up BYD config entry %s", entry.entry_id)
    hass.data.setdefault(DOMAIN, {})

    # Ensure a device fingerprint exists (backfill for pre-existing entries)
    if CONF_DEVICE_PROFILE not in entry.data:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_DEVICE_PROFILE: await async_generate_device_profile(hass),
            },
        )

    session = async_get_clientsession(hass)
    api = BydApi(hass, entry, session)

    poll_interval = _sanitize_interval(
        entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
        DEFAULT_POLL_INTERVAL,
        MIN_POLL_INTERVAL,
        MAX_POLL_INTERVAL,
    )
    gps_interval = _sanitize_interval(
        entry.options.get(CONF_GPS_POLL_INTERVAL, DEFAULT_GPS_POLL_INTERVAL),
        DEFAULT_GPS_POLL_INTERVAL,
        MIN_GPS_POLL_INTERVAL,
        MAX_GPS_POLL_INTERVAL,
    )
    smart_gps = entry.options.get(CONF_SMART_GPS_POLLING, DEFAULT_SMART_GPS_POLLING)
    gps_active = _sanitize_interval(
        entry.options.get(CONF_GPS_ACTIVE_INTERVAL, DEFAULT_GPS_ACTIVE_INTERVAL),
        DEFAULT_GPS_ACTIVE_INTERVAL,
        MIN_GPS_ACTIVE_INTERVAL,
        MAX_GPS_ACTIVE_INTERVAL,
    )
    gps_inactive = _sanitize_interval(
        entry.options.get(CONF_GPS_INACTIVE_INTERVAL, DEFAULT_GPS_INACTIVE_INTERVAL),
        DEFAULT_GPS_INACTIVE_INTERVAL,
        MIN_GPS_INACTIVE_INTERVAL,
        MAX_GPS_INACTIVE_INTERVAL,
    )

    async def _fetch_vehicles(client: BydClient) -> list:
        return await client.get_vehicles()

    vehicles = await api.async_call(_fetch_vehicles)
    if not vehicles:
        raise ConfigEntryNotReady("No vehicles available for this account")

    _LOGGER.debug(
        "Discovered %s BYD vehicle(s) for entry %s",
        len(vehicles),
        entry.entry_id,
    )

    coordinators: dict[str, BydDataUpdateCoordinator] = {}
    gps_coordinators: dict[str, BydGpsUpdateCoordinator] = {}

    for vehicle in vehicles:
        vin = vehicle.vin
        telemetry_coordinator = BydDataUpdateCoordinator(
            hass,
            api,
            vehicle,
            vin,
            poll_interval,
        )
        gps_coordinator = BydGpsUpdateCoordinator(
            hass,
            api,
            vehicle,
            vin,
            gps_interval,
            telemetry_coordinator=telemetry_coordinator,
            smart_polling=smart_gps,
            active_interval=gps_active,
            inactive_interval=gps_inactive,
        )
        coordinators[vin] = telemetry_coordinator
        gps_coordinators[vin] = gps_coordinator

    # Wire MQTT push early so vehicleInfo messages arriving during the
    # first refresh are dispatched to coordinators instead of being dropped.
    api.register_coordinators(coordinators)

    try:
        _LOGGER.debug("Running first refresh for BYD telemetry coordinators")
        for coordinator in coordinators.values():
            await coordinator.async_config_entry_first_refresh()
        _LOGGER.debug("Running first refresh for BYD GPS coordinators")
        for gps_coordinator in gps_coordinators.values():
            await gps_coordinator.async_config_entry_first_refresh()
    except Exception as exc:  # noqa: BLE001
        raise ConfigEntryNotReady from exc

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinators": coordinators,
        "gps_coordinators": gps_coordinators,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # --- Register domain services (once, on first entry) ---
    _async_register_services(hass)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    _LOGGER.debug("BYD config entry %s setup complete", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading BYD config entry %s", entry.entry_id)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if entry_data and "api" in entry_data:
            await entry_data["api"].async_shutdown()
        _LOGGER.debug("Unloaded BYD config entry %s", entry.entry_id)
        # Unregister services when no entries remain.
        if not hass.data.get(DOMAIN):
            _async_unregister_services(hass)
    else:
        _LOGGER.debug("BYD config entry %s unload returned False", entry.entry_id)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    _LOGGER.debug("Reloading BYD config entry %s", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


# ------------------------------------------------------------------
# Service helpers
# ------------------------------------------------------------------

_SERVICE_FETCH_REALTIME = "fetch_realtime"
_SERVICE_FETCH_GPS = "fetch_gps"
_SERVICE_FETCH_HVAC = "fetch_hvac"
_SERVICE_SET_CHARGING_SCHEDULE = "set_charging_schedule"
_SERVICE_RENAME_VEHICLE = "rename_vehicle"

_ALL_SERVICES = (
    _SERVICE_FETCH_REALTIME,
    _SERVICE_FETCH_GPS,
    _SERVICE_FETCH_HVAC,
    _SERVICE_SET_CHARGING_SCHEDULE,
    _SERVICE_RENAME_VEHICLE,
)

_SET_CHARGING_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("target_soc"): vol.All(
            vol.Coerce(int), vol.Range(min=20, max=100)
        ),
        vol.Required("start_hour"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=23)
        ),
        vol.Required("start_minute"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=59)
        ),
        vol.Required("end_hour"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=23)
        ),
        vol.Required("end_minute"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=59)
        ),
    }
)

_RENAME_VEHICLE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=32)),
    }
)


def _resolve_vins_from_call(
    hass: HomeAssistant,
    call: ServiceCall,
) -> list[tuple[str, str]]:
    """Resolve (entry_id, vin) pairs from device targets in a service call.

    Raises ``HomeAssistantError`` when no valid targets can be resolved.
    """
    device_ids: list[str] = call.data.get("device_id", [])
    if isinstance(device_ids, str):
        device_ids = [device_ids]

    dev_reg = dr.async_get(hass)
    results: list[tuple[str, str]] = []

    for device_id in device_ids:
        device = dev_reg.async_get(device_id)
        if device is None:
            continue
        for identifier in device.identifiers:
            if identifier[0] == DOMAIN:
                vin = identifier[1]
                # Find which config entry owns this VIN.
                for entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
                    coordinators = entry_data.get("coordinators", {})
                    if vin in coordinators:
                        results.append((entry_id, vin))
                        break

    if not results:
        raise HomeAssistantError("No BYD vehicle devices found for the given targets")
    return results


def _get_coordinators(
    hass: HomeAssistant,
    entry_id: str,
    vin: str,
) -> tuple[BydDataUpdateCoordinator, BydGpsUpdateCoordinator | None]:
    """Return (telemetry, gps) coordinators for an entry/vin pair."""
    entry_data: dict[str, Any] = hass.data[DOMAIN][entry_id]
    telemetry: BydDataUpdateCoordinator = entry_data["coordinators"][vin]
    gps: BydGpsUpdateCoordinator | None = entry_data.get("gps_coordinators", {}).get(
        vin
    )
    return telemetry, gps


def _async_register_services(hass: HomeAssistant) -> None:
    """Register domain services (idempotent — safe to call multiple times)."""

    if hass.services.has_service(DOMAIN, _SERVICE_FETCH_REALTIME):
        return  # Already registered.

    async def _handle_fetch_realtime(call: ServiceCall) -> None:
        for entry_id, vin in _resolve_vins_from_call(hass, call):
            coordinator, _ = _get_coordinators(hass, entry_id, vin)
            await coordinator.async_fetch_realtime()

    async def _handle_fetch_gps(call: ServiceCall) -> None:
        for entry_id, vin in _resolve_vins_from_call(hass, call):
            _, gps = _get_coordinators(hass, entry_id, vin)
            if gps is not None:
                await gps.async_fetch_gps()

    async def _handle_fetch_hvac(call: ServiceCall) -> None:
        for entry_id, vin in _resolve_vins_from_call(hass, call):
            coordinator, _ = _get_coordinators(hass, entry_id, vin)
            await coordinator.async_fetch_hvac()

    async def _handle_set_charging_schedule(call: ServiceCall) -> None:
        from .pybyd_ext import SmartChargingConfig, save_charging_schedule

        for entry_id, vin in _resolve_vins_from_call(hass, call):
            entry_data: dict[str, Any] = hass.data[DOMAIN][entry_id]
            api: BydApi = entry_data["api"]
            config = SmartChargingConfig(
                target_soc=call.data["target_soc"],
                start_hour=call.data["start_hour"],
                start_minute=call.data["start_minute"],
                end_hour=call.data["end_hour"],
                end_minute=call.data["end_minute"],
            )

            async def _call(client: BydClient) -> dict:
                return await save_charging_schedule(client, vin, config)

            await api.async_call(_call)

    async def _handle_rename_vehicle(call: ServiceCall) -> None:
        from .pybyd_ext import rename_vehicle

        for entry_id, vin in _resolve_vins_from_call(hass, call):
            entry_data: dict[str, Any] = hass.data[DOMAIN][entry_id]
            api: BydApi = entry_data["api"]
            new_name = call.data["name"]

            async def _call(client: BydClient) -> dict:
                return await rename_vehicle(client, vin, name=new_name)

            await api.async_call(_call)

    hass.services.async_register(
        DOMAIN, _SERVICE_FETCH_REALTIME, _handle_fetch_realtime
    )
    hass.services.async_register(DOMAIN, _SERVICE_FETCH_GPS, _handle_fetch_gps)
    hass.services.async_register(DOMAIN, _SERVICE_FETCH_HVAC, _handle_fetch_hvac)
    hass.services.async_register(
        DOMAIN,
        _SERVICE_SET_CHARGING_SCHEDULE,
        _handle_set_charging_schedule,
        schema=_SET_CHARGING_SCHEDULE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        _SERVICE_RENAME_VEHICLE,
        _handle_rename_vehicle,
        schema=_RENAME_VEHICLE_SCHEMA,
    )

    _LOGGER.debug("Registered %s domain services", len(_ALL_SERVICES))


def _async_unregister_services(hass: HomeAssistant) -> None:
    """Remove domain services when the last config entry is unloaded."""
    for service in _ALL_SERVICES:
        hass.services.async_remove(DOMAIN, service)
    _LOGGER.debug("Unregistered %s domain services", len(_ALL_SERVICES))
