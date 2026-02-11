"""Data coordinators for BYD Vehicle."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from pybyd import BydApiError, BydAuthenticationError, BydClient, BydTransportError
from pybyd.config import BydConfig
from pybyd.models.vehicle import Vehicle

from .const import CONF_BASE_URL, CONF_COUNTRY_CODE, CONF_LANGUAGE, DEFAULT_LANGUAGE, DOMAIN

_LOGGER = logging.getLogger(__name__)


def _get_vehicle_name(vehicle: Vehicle) -> str:
    return vehicle.auto_alias or vehicle.model_name or vehicle.vin


def _dataclass_to_dict(value: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(value):
        data = dataclasses.asdict(value)
        data.pop("raw", None)
        return data
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if k != "raw"}
    if hasattr(value, "__dict__"):
        data = dict(value.__dict__)
        data.pop("raw", None)
        return data
    return {"value": value}


def extract_raw(value: Any) -> dict[str, Any] | None:
    """Return raw payload if available on a model or dict."""
    if dataclasses.is_dataclass(value) and hasattr(value, "raw"):
        raw = getattr(value, "raw")
        return raw if isinstance(raw, dict) else None
    if isinstance(value, dict):
        raw = value.get("raw")
        return raw if isinstance(raw, dict) else None
    if hasattr(value, "raw"):
        raw = getattr(value, "raw")
        return raw if isinstance(raw, dict) else None
    return None


def expand_metrics(value: Any) -> dict[str, Any]:
    """Merge dataclass fields with raw payload keys."""
    data = _dataclass_to_dict(value)
    raw = extract_raw(value)
    if raw:
        for key, val in raw.items():
            if key in data:
                continue
            data[f"raw_{key}"] = val
    return data


class BydApi:
    """Thin wrapper around the pybyd client."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, session: Any) -> None:
        self._hass = hass
        self._entry = entry
        self._session = session
        time_zone = hass.config.time_zone or "UTC"
        self._config = BydConfig(
            username=entry.data["username"],
            password=entry.data["password"],
            base_url=entry.data[CONF_BASE_URL],
            country_code=entry.data.get(CONF_COUNTRY_CODE, "NL"),
            language=entry.data.get(CONF_LANGUAGE, DEFAULT_LANGUAGE),
            time_zone=time_zone,
        )

    @property
    def config(self) -> BydConfig:
        return self._config

    async def async_call(self, handler: Any) -> Any:
        try:
            async with BydClient(self._config, session=self._session) as client:
                await client.login()
                return await handler(client)
        except (BydAuthenticationError, BydApiError, BydTransportError) as exc:
            raise UpdateFailed(str(exc)) from exc


class BydDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for normal telemetry updates."""

    def __init__(self, hass: HomeAssistant, api: BydApi, poll_interval: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_telemetry",
            update_interval=timedelta(seconds=poll_interval),
        )
        self._api = api

    async def _async_update_data(self) -> dict[str, Any]:
        async def _fetch(client: BydClient) -> dict[str, Any]:
            vehicles = await client.get_vehicles()
            vehicle_map = {vehicle.vin: vehicle for vehicle in vehicles}

            async def _fetch_for_vin(vin: str) -> tuple[str, Any, Any]:
                realtime = await client.get_vehicle_realtime(vin)
                energy = await client.get_energy_consumption(vin)
                return vin, realtime, energy

            tasks = [_fetch_for_vin(vin) for vin in vehicle_map]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            realtime_map: dict[str, Any] = {}
            energy_map: dict[str, Any] = {}
            for result in results:
                if isinstance(result, Exception):
                    _LOGGER.warning("Telemetry update failed: %s", result)
                    continue
                vin, realtime, energy = result
                realtime_map[vin] = realtime
                energy_map[vin] = energy

            return {
                "vehicles": vehicle_map,
                "realtime": realtime_map,
                "energy": energy_map,
            }

        return await self._api.async_call(_fetch)


class BydGpsUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for GPS updates."""

    def __init__(self, hass: HomeAssistant, api: BydApi, poll_interval: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_gps",
            update_interval=timedelta(seconds=poll_interval),
        )
        self._api = api

    async def _async_update_data(self) -> dict[str, Any]:
        async def _fetch(client: BydClient) -> dict[str, Any]:
            vehicles = await client.get_vehicles()
            vehicle_map = {vehicle.vin: vehicle for vehicle in vehicles}

            async def _fetch_for_vin(vin: str) -> tuple[str, Any]:
                gps = await client.get_gps_info(vin)
                return vin, gps

            tasks = [_fetch_for_vin(vin) for vin in vehicle_map]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            gps_map: dict[str, Any] = {}
            for result in results:
                if isinstance(result, Exception):
                    _LOGGER.warning("GPS update failed: %s", result)
                    continue
                vin, gps = result
                gps_map[vin] = gps

            return {
                "vehicles": vehicle_map,
                "gps": gps_map,
            }

        return await self._api.async_call(_fetch)


def get_vehicle_display(vehicle: Vehicle) -> str:
    """Return a friendly name for a vehicle."""
    return _get_vehicle_name(vehicle)


def flatten_metrics(value: Any) -> dict[str, Any]:
    """Flatten a dataclass-like model into a plain dict."""
    return _dataclass_to_dict(value)
