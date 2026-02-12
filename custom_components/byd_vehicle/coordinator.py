"""Data coordinators for BYD Vehicle."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pybyd import (
    BydApiError,
    BydAuthenticationError,
    BydClient,
    BydRemoteControlError,
    BydSessionExpiredError,
    BydTransportError,
    RemoteControlResult,
)
from pybyd.config import BydConfig, DeviceProfile
from pybyd.models.vehicle import Vehicle

from .const import (
    CONF_BASE_URL,
    CONF_CONTROL_PIN,
    CONF_COUNTRY_CODE,
    CONF_DEVICE_PROFILE,
    CONF_LANGUAGE,
    DEFAULT_LANGUAGE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _get_vehicle_name(vehicle: Vehicle) -> str:
    return vehicle.auto_alias or vehicle.model_name or vehicle.vin


class BydApi:
    """Thin wrapper around the pybyd client."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, session: Any) -> None:
        self._hass = hass
        self._entry = entry
        self._http_session = session
        time_zone = hass.config.time_zone or "UTC"
        device = DeviceProfile(**entry.data[CONF_DEVICE_PROFILE])
        self._config = BydConfig(
            username=entry.data["username"],
            password=entry.data["password"],
            base_url=entry.data[CONF_BASE_URL],
            country_code=entry.data.get(CONF_COUNTRY_CODE, "NL"),
            language=entry.data.get(CONF_LANGUAGE, DEFAULT_LANGUAGE),
            time_zone=time_zone,
            device=device,
            control_pin=entry.data.get(CONF_CONTROL_PIN) or None,
        )
        self._last_remote_results: dict[tuple[str, str], dict[str, Any]] = {}
        self._client: BydClient | None = None

    @property
    def config(self) -> BydConfig:
        return self._config

    def get_last_remote_result(self, vin: str, command: str) -> dict[str, Any] | None:
        return self._last_remote_results.get((vin, command))

    def _store_remote_result(
        self,
        vin: str,
        command: str,
        result: RemoteControlResult | None,
        error: Exception | None = None,
    ) -> None:
        data: dict[str, Any] = {
            "command": command,
            "success": False,
            "control_state": 0,
            "request_serial": None,
        }
        if result is not None:
            data.update(
                {
                    "success": result.success,
                    "control_state": result.control_state,
                    "request_serial": result.request_serial,
                    "raw": result.raw,
                }
            )
        if error is not None:
            data["error"] = str(error)
            data["error_type"] = type(error).__name__
        self._last_remote_results[(vin, command)] = data

    async def _ensure_client(self) -> BydClient:
        """Return a ready-to-use client, creating one if needed.

        The client's own ``ensure_session()`` handles login and token
        expiry transparently — we only manage the transport lifecycle.
        """
        if self._client is None:
            self._client = BydClient(
                self._config, session=self._http_session
            )
            await self._client.__aenter__()
        return self._client

    async def _invalidate_client(self) -> None:
        """Tear down the current client so the next call creates a fresh one."""
        if self._client is not None:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    async def async_call(
        self,
        handler: Any,
        *,
        vin: str | None = None,
        command: str | None = None,
    ) -> Any:
        """Execute *handler(client)* with automatic session management.

        The pybyd client handles login and session-expiry retries
        internally via ``ensure_session()``.  We only need to recreate
        the client on hard transport failures.
        """
        try:
            client = await self._ensure_client()
            result = await handler(client)
            if isinstance(result, RemoteControlResult) and vin and command:
                self._store_remote_result(vin, command, result)
            return result
        except BydSessionExpiredError:
            # Session invalidated elsewhere; reconnect and retry once.
            await self._invalidate_client()
            try:
                client = await self._ensure_client()
                result = await handler(client)
                if isinstance(result, RemoteControlResult) and vin and command:
                    self._store_remote_result(vin, command, result)
                return result
            except BydAuthenticationError as exc:
                if vin and command:
                    self._store_remote_result(vin, command, None, exc)
                raise ConfigEntryAuthFailed(str(exc)) from exc
            except BydSessionExpiredError as exc:
                if vin and command:
                    self._store_remote_result(vin, command, None, exc)
                raise ConfigEntryAuthFailed(str(exc)) from exc
        except BydRemoteControlError as exc:
            if vin and command:
                self._store_remote_result(vin, command, None, exc)
            raise UpdateFailed(str(exc)) from exc
        except BydTransportError as exc:
            if vin and command:
                self._store_remote_result(vin, command, None, exc)
            # Hard transport error — tear down so next call reconnects
            await self._invalidate_client()
            raise UpdateFailed(str(exc)) from exc
        except BydAuthenticationError as exc:
            if vin and command:
                self._store_remote_result(vin, command, None, exc)
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except BydApiError as exc:
            if vin and command:
                self._store_remote_result(vin, command, None, exc)
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

            async def _fetch_for_vin(
                vin: str,
            ) -> tuple[str, Any, Any, Any, Any]:
                realtime = None
                energy = None
                hvac = None
                charging = None
                try:
                    realtime = await client.get_vehicle_realtime(vin)
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("Realtime fetch failed for %s: %s", vin, exc)
                try:
                    energy = await client.get_energy_consumption(vin)
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("Energy fetch failed for %s: %s", vin, exc)
                try:
                    hvac = await client.get_hvac_status(vin)
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("HVAC fetch failed for %s: %s", vin, exc)
                try:
                    charging = await client.get_charging_status(vin)
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("Charging fetch failed for %s: %s", vin, exc)
                return vin, realtime, energy, hvac, charging

            tasks = [_fetch_for_vin(vin) for vin in vehicle_map]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            realtime_map: dict[str, Any] = {}
            energy_map: dict[str, Any] = {}
            hvac_map: dict[str, Any] = {}
            charging_map: dict[str, Any] = {}
            for result in results:
                if isinstance(result, BaseException):
                    _LOGGER.warning("Telemetry update failed: %s", result)
                    continue
                vin, realtime, energy, hvac, charging = result
                if realtime is not None:
                    realtime_map[vin] = realtime
                if energy is not None:
                    energy_map[vin] = energy
                if hvac is not None:
                    hvac_map[vin] = hvac
                if charging is not None:
                    charging_map[vin] = charging

            if vehicle_map and not any(
                [realtime_map, energy_map, hvac_map, charging_map]
            ):
                raise UpdateFailed(
                    "All telemetry fetches failed for all vehicles"
                )

            return {
                "vehicles": vehicle_map,
                "realtime": realtime_map,
                "energy": energy_map,
                "hvac": hvac_map,
                "charging": charging_map,
            }

        return await self._api.async_call(_fetch)


class BydGpsUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for GPS updates with optional smart polling."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: BydApi,
        poll_interval: int,
        *,
        telemetry_coordinator: BydDataUpdateCoordinator | None = None,
        smart_polling: bool = False,
        active_interval: int = 30,
        inactive_interval: int = 600,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_gps",
            update_interval=timedelta(seconds=poll_interval),
        )
        self._api = api
        self._telemetry_coordinator = telemetry_coordinator
        self._smart_polling = smart_polling
        self._active_interval = timedelta(seconds=active_interval)
        self._inactive_interval = timedelta(seconds=inactive_interval)
        self._last_smart_state: bool | None = None

    def _is_any_vehicle_moving(self, data: dict[str, Any]) -> bool:
        """Check if any vehicle is moving based on GPS speed."""
        gps_map = data.get("gps", {})
        if not gps_map:
            _LOGGER.debug("Smart GPS: no GPS data available yet")
            return False
        for vin, gps in gps_map.items():
            if gps is None:
                continue
            speed = getattr(gps, "speed", None)
            _LOGGER.debug(
                "Smart GPS: VIN %s speed=%s",
                vin[-6:],
                speed,
            )
            if speed is not None and speed > 0:
                return True
        return False

    def _adjust_interval(self, data: dict[str, Any]) -> None:
        """Adjust the polling interval based on vehicle movement."""
        if not self._smart_polling:
            return
        is_moving = self._is_any_vehicle_moving(data)
        new_interval = self._active_interval if is_moving else self._inactive_interval
        if self.update_interval != new_interval:
            _LOGGER.info(
                "Smart GPS polling: vehicle %s, interval %ss -> %ss",
                "moving" if is_moving else "stationary",
                self.update_interval.total_seconds(),
                new_interval.total_seconds(),
            )
            self.update_interval = new_interval
            self._last_smart_state = is_moving

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
                if isinstance(result, BaseException):
                    _LOGGER.warning("GPS update failed: %s", result)
                    continue
                vin, gps = result
                if gps is not None:
                    gps_map[vin] = gps

            if vehicle_map and not gps_map:
                raise UpdateFailed(
                    "GPS fetch failed for all vehicles"
                )

            return {
                "vehicles": vehicle_map,
                "gps": gps_map,
            }

        data = await self._api.async_call(_fetch)
        self._adjust_interval(data)
        return data


def get_vehicle_display(vehicle: Vehicle) -> str:
    """Return a friendly name for a vehicle."""
    return _get_vehicle_name(vehicle)
