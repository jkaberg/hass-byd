"""Data coordinators for BYD Vehicle."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pybyd import (
    BydApiError,
    BydAuthenticationError,
    BydClient,
    BydControlPasswordError,
    BydEndpointNotSupportedError,
    BydRateLimitError,
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
    CONF_DEBUG_DUMPS,
    CONF_DEVICE_PROFILE,
    CONF_LANGUAGE,
    DEFAULT_DEBUG_DUMPS,
    DEFAULT_LANGUAGE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _get_vehicle_name(vehicle: Vehicle) -> str:
    return vehicle.model_name or vehicle.vin


def _normalize_epoch(value: Any) -> datetime | None:
    """Convert epoch-like values (sec/ms) to UTC datetime."""
    if value is None:
        return None
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    if ts > 1_000_000_000_000:
        ts = ts / 1000
    try:
        return datetime.fromtimestamp(ts, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


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
        self._unsupported_remote_commands: dict[str, set[str]] = {}
        self._client: BydClient | None = None
        self._debug_dumps_enabled = entry.options.get(
            CONF_DEBUG_DUMPS,
            DEFAULT_DEBUG_DUMPS,
        )
        self._last_transmissions: dict[str, datetime] = {}
        self._debug_dump_dir = Path(hass.config.path(".storage/byd_vehicle_debug"))
        self._reload_scheduled = False
        # Serialize all BYD cloud calls so telemetry polls and remote
        # commands never overlap (BYD returns 6024 for concurrent ops).
        self._api_lock = asyncio.Lock()

    def update_last_transmission(
        self,
        vin: str,
        *,
        realtime: Any | None = None,
        gps: Any | None = None,
        charging: Any | None = None,
    ) -> None:
        """Store latest observed transmission timestamp for a VIN."""
        candidates = [
            (
                _normalize_epoch(getattr(realtime, "timestamp", None))
                if realtime is not None
                else None
            ),
            (
                _normalize_epoch(getattr(gps, "gps_timestamp", None))
                if gps is not None
                else None
            ),
            (
                _normalize_epoch(getattr(charging, "update_time", None))
                if charging is not None
                else None
            ),
        ]
        valid_candidates = [
            candidate for candidate in candidates if candidate is not None
        ]
        if not valid_candidates:
            return
        latest = max(valid_candidates)
        current = self._last_transmissions.get(vin)
        if current is None or latest > current:
            self._last_transmissions[vin] = latest

    def get_last_transmission(self, vin: str) -> datetime | None:
        """Return last known transmission timestamp for a VIN."""
        return self._last_transmissions.get(vin)

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return BydApi._json_safe(dataclasses.asdict(value))
        if isinstance(value, dict):
            return {str(key): BydApi._json_safe(inner) for key, inner in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [BydApi._json_safe(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if hasattr(value, "value"):
            enum_value = getattr(value, "value", None)
            if isinstance(enum_value, (str, int, float, bool)):
                return enum_value
            return str(value)
        return str(value)

    def _write_debug_dump(self, category: str, payload: dict[str, Any]) -> None:
        if not self._debug_dumps_enabled:
            return
        try:
            self._debug_dump_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%fZ")
            file_path = self._debug_dump_dir / f"{timestamp}_{category}.json"
            file_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Failed to write BYD debug dump", exc_info=True)

    def _record_transport_trace(self, payload: dict[str, Any]) -> None:
        self._write_debug_dump("api_trace", self._json_safe(payload))

    @property
    def config(self) -> BydConfig:
        return self._config

    def get_last_remote_result(self, vin: str, command: str) -> dict[str, Any] | None:
        return self._last_remote_results.get((vin, command))

    @staticmethod
    def _related_command_names(command: str) -> set[str]:
        related = {command}
        pairs = (
            ("start_climate", "stop_climate"),
            ("car_on", "car_off"),
            ("battery_heat_on", "battery_heat_off"),
            ("steering_wheel_heat_on", "steering_wheel_heat_off"),
            ("lock", "unlock"),
        )
        for first, second in pairs:
            if command in (first, second):
                related.update({first, second})
                break
        return related

    def mark_remote_command_unsupported(self, vin: str, command: str) -> None:
        unsupported = self._unsupported_remote_commands.setdefault(vin, set())
        unsupported.update(self._related_command_names(command))

    def is_remote_command_supported(self, vin: str, command: str) -> bool:
        return command not in self._unsupported_remote_commands.get(vin, set())

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
            if isinstance(error, BydApiError):
                data["error_code"] = error.code
                data["error_endpoint"] = error.endpoint
        self._last_remote_results[(vin, command)] = data

    async def _ensure_client(self) -> BydClient:
        """Return a ready-to-use client, creating one if needed.

        The client's own ``ensure_session()`` handles login and token
        expiry transparently — we only manage the transport lifecycle.
        """
        if self._client is None:
            self._client = BydClient(
                self._config,
                session=self._http_session,
                response_trace_recorder=(
                    self._record_transport_trace if self._debug_dumps_enabled else None
                ),
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

        All calls are serialized through ``_api_lock`` so that
        telemetry polls and remote-control commands never overlap on
        BYD's cloud (which returns code 6024 for concurrent ops).

        The pybyd client handles login and session-expiry retries
        internally via ``ensure_session()``.  We only need to recreate
        the client on hard transport failures.
        """
        async with self._api_lock:
            return await self._async_call_inner(handler, vin=vin, command=command)

    async def _async_call_inner(
        self,
        handler: Any,
        *,
        vin: str | None = None,
        command: str | None = None,
    ) -> Any:
        """Inner call logic (must be called under ``_api_lock``)."""
        try:
            client = await self._ensure_client()
            result = await handler(client)
            self._reload_scheduled = False
            if isinstance(result, RemoteControlResult) and vin and command:
                self._store_remote_result(vin, command, result)
            return result
        except BydSessionExpiredError:
            # Session invalidated elsewhere; reconnect and retry once.
            await self._invalidate_client()
            try:
                client = await self._ensure_client()
                result = await handler(client)
                self._reload_scheduled = False
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
                if not self._reload_scheduled:
                    self._reload_scheduled = True
                    self._hass.async_create_task(
                        self._hass.config_entries.async_reload(self._entry.entry_id)
                    )
                raise UpdateFailed(str(exc)) from exc
        except BydRemoteControlError as exc:
            if vin and command:
                self._store_remote_result(vin, command, None, exc)
            raise UpdateFailed(str(exc)) from exc
        except BydControlPasswordError as exc:
            if vin and command:
                self._store_remote_result(vin, command, None, exc)
            raise UpdateFailed(
                "Control PIN rejected or cloud control temporarily locked"
            ) from exc
        except BydRateLimitError as exc:
            if vin and command:
                self._store_remote_result(vin, command, None, exc)
            raise UpdateFailed(
                "Command rate limited by BYD cloud, please retry shortly"
            ) from exc
        except BydEndpointNotSupportedError as exc:
            if vin and command:
                self._store_remote_result(vin, command, None, exc)
                self.mark_remote_command_unsupported(vin, command)
            raise UpdateFailed("Feature not supported for this vehicle/region") from exc
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
    """Coordinator for telemetry updates for a single VIN."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: BydApi,
        vin: str,
        poll_interval: int,
        *,
        active_interval: int,
        inactive_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_telemetry_{vin[-6:]}",
            update_interval=timedelta(seconds=poll_interval),
        )
        self._api = api
        self._vin = vin
        self._active_interval = timedelta(seconds=active_interval)
        self._inactive_interval = timedelta(seconds=inactive_interval)
        self._current_interval = timedelta(seconds=poll_interval)

    def _desired_interval(self) -> timedelta:
        """Determine poll interval from transmission recency."""
        last_transmission = self._api.get_last_transmission(self._vin)
        if last_transmission is None:
            return self._inactive_interval
        age = datetime.now(tz=UTC) - last_transmission
        if age <= self._active_interval:
            return self._active_interval
        return self._inactive_interval

    def _adjust_interval(self) -> None:
        """Apply adaptive interval for this VIN."""
        new_interval = self._desired_interval()
        if self._current_interval == new_interval:
            return
        _LOGGER.info(
            "Telemetry adaptive polling VIN %s: %ss -> %ss",
            self._vin[-6:],
            self._current_interval.total_seconds(),
            new_interval.total_seconds(),
        )
        self._current_interval = new_interval
        self.update_interval = new_interval

    async def _async_update_data(self) -> dict[str, Any]:
        async def _fetch(client: BydClient) -> dict[str, Any]:
            vehicles = await client.get_vehicles()
            vehicle = next(
                (vehicle for vehicle in vehicles if vehicle.vin == self._vin),
                None,
            )
            if vehicle is None:
                raise UpdateFailed(f"Vehicle {self._vin} no longer present")
            vehicle_map = {self._vin: vehicle}

            auth_errors = (BydAuthenticationError, BydSessionExpiredError)
            realtime = None
            energy = None
            hvac = None
            charging = None
            try:
                realtime = await client.get_vehicle_realtime(self._vin)
            except auth_errors:
                raise
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Realtime fetch failed for %s: %s", self._vin, exc)
            try:
                energy = await client.get_energy_consumption(self._vin)
            except auth_errors:
                raise
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Energy fetch failed for %s: %s", self._vin, exc)
            try:
                hvac = await client.get_hvac_status(self._vin)
            except auth_errors:
                raise
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("HVAC fetch failed for %s: %s", self._vin, exc)
            try:
                charging = await client.get_charging_status(self._vin)
            except auth_errors:
                raise
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Charging fetch failed for %s: %s", self._vin, exc)

            realtime_map: dict[str, Any] = {}
            energy_map: dict[str, Any] = {}
            hvac_map: dict[str, Any] = {}
            charging_map: dict[str, Any] = {}
            if realtime is not None:
                realtime_map[self._vin] = realtime
            if energy is not None:
                energy_map[self._vin] = energy
            if hvac is not None:
                hvac_map[self._vin] = hvac
            if charging is not None:
                charging_map[self._vin] = charging

            if not any([realtime_map, energy_map, hvac_map, charging_map]):
                raise UpdateFailed(f"All telemetry fetches failed for {self._vin}")

            return {
                "vehicles": vehicle_map,
                "realtime": realtime_map,
                "energy": energy_map,
                "hvac": hvac_map,
                "charging": charging_map,
            }

        data = await self._api.async_call(_fetch)
        self._api.update_last_transmission(
            self._vin,
            realtime=data.get("realtime", {}).get(self._vin),
            charging=data.get("charging", {}).get(self._vin),
        )
        self._adjust_interval()
        return data


class BydGpsUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for GPS updates for a single VIN with adaptive polling."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: BydApi,
        vin: str,
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
            name=f"{DOMAIN}_gps_{vin[-6:]}",
            update_interval=timedelta(seconds=poll_interval),
        )
        self._api = api
        self._vin = vin
        self._telemetry_coordinator = telemetry_coordinator
        self._smart_polling = smart_polling
        self._active_interval = timedelta(seconds=active_interval)
        self._inactive_interval = timedelta(seconds=inactive_interval)
        self._current_interval = timedelta(seconds=poll_interval)
        self._last_smart_state: bool | None = None

    def _is_vehicle_moving(self, data: dict[str, Any]) -> bool:
        """Check if this VIN is moving based on known speed data."""
        gps_map = data.get("gps", {})
        telemetry_data = (
            self._telemetry_coordinator.data if self._telemetry_coordinator else None
        )
        realtime_map = (
            telemetry_data.get("realtime", {})
            if isinstance(telemetry_data, dict)
            else {}
        )

        gps = gps_map.get(self._vin)
        realtime = realtime_map.get(self._vin)

        realtime_speed = (
            getattr(realtime, "speed", None) if realtime is not None else None
        )
        gps_speed = getattr(gps, "speed", None) if gps is not None else None
        speed = realtime_speed if realtime_speed is not None else gps_speed
        if realtime_speed is not None:
            speed_source = "realtime"
        elif gps_speed is not None:
            speed_source = "gps"
        else:
            speed_source = "none"

        _LOGGER.debug(
            "Smart GPS: VIN %s speed=%s source=%s (realtime=%s gps=%s)",
            self._vin[-6:],
            speed,
            speed_source,
            realtime_speed,
            gps_speed,
        )
        return speed is not None and speed > 0

    def _desired_interval(self) -> timedelta:
        """Determine base interval from transmission recency."""
        last_transmission = self._api.get_last_transmission(self._vin)
        if last_transmission is None:
            return self._inactive_interval
        age = datetime.now(tz=UTC) - last_transmission
        if age <= self._active_interval:
            return self._active_interval
        return self._inactive_interval

    def _adjust_interval(self, data: dict[str, Any]) -> None:
        """Adjust polling interval based on transmission and optional movement."""
        new_interval = self._desired_interval()
        is_moving = False
        if self._smart_polling:
            is_moving = self._is_vehicle_moving(data)
            if is_moving and new_interval > self._active_interval:
                new_interval = self._active_interval

        if self._current_interval != new_interval:
            _LOGGER.info(
                "GPS adaptive polling VIN %s (%s): %ss -> %ss",
                self._vin[-6:],
                "moving" if is_moving else "idle",
                self._current_interval.total_seconds(),
                new_interval.total_seconds(),
            )
            self._current_interval = new_interval
            self.update_interval = new_interval
        self._last_smart_state = is_moving

    async def _async_update_data(self) -> dict[str, Any]:
        async def _fetch(client: BydClient) -> dict[str, Any]:
            vehicles = await client.get_vehicles()
            vehicle = next(
                (vehicle for vehicle in vehicles if vehicle.vin == self._vin),
                None,
            )
            if vehicle is None:
                raise UpdateFailed(f"Vehicle {self._vin} no longer present")
            vehicle_map = {self._vin: vehicle}

            auth_errors = (BydAuthenticationError, BydSessionExpiredError)
            gps = None
            try:
                gps = await client.get_gps_info(self._vin)
            except auth_errors:
                raise
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("GPS fetch failed for %s: %s", self._vin, exc)

            gps_map: dict[str, Any] = {}
            if gps is not None:
                gps_map[self._vin] = gps

            if not gps_map:
                raise UpdateFailed(f"GPS fetch failed for {self._vin}")

            return {
                "vehicles": vehicle_map,
                "gps": gps_map,
            }

        data = await self._api.async_call(_fetch)
        self._api.update_last_transmission(
            self._vin,
            gps=data.get("gps", {}).get(self._vin),
        )
        self._adjust_interval(data)
        return data


def get_vehicle_display(vehicle: Vehicle) -> str:
    """Return a friendly name for a vehicle."""
    return _get_vehicle_name(vehicle)
