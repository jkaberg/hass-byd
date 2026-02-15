"""Data coordinators for BYD Vehicle."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
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
    BydSessionExpiredError,
    BydTransportError,
)
from pybyd.config import BydConfig, DeviceProfile
from pybyd.models.charging import ChargingStatus
from pybyd.models.energy import EnergyConsumption
from pybyd.models.gps import GpsInfo
from pybyd.models.hvac import HvacStatus
from pybyd.models.realtime import ChargingState, VehicleRealtimeData
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


# Error tuples shared by telemetry and GPS _fetch closures.
_AUTH_ERRORS = (BydAuthenticationError, BydSessionExpiredError)
_RECOVERABLE_ERRORS = (
    BydApiError,
    BydTransportError,
    BydRateLimitError,
    BydEndpointNotSupportedError,
)


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
        self._client: BydClient | None = None
        self._debug_dumps_enabled = entry.options.get(
            CONF_DEBUG_DUMPS,
            DEFAULT_DEBUG_DUMPS,
        )
        self._debug_dump_dir = Path(hass.config.path(".storage/byd_vehicle_debug"))
        self._coordinators: dict[str, BydDataUpdateCoordinator] = {}
        _LOGGER.debug(
            "BYD API initialized: entry_id=%s, region=%s, language=%s",
            entry.entry_id,
            entry.data[CONF_BASE_URL],
            entry.data.get(CONF_LANGUAGE, DEFAULT_LANGUAGE),
        )

    def register_coordinators(
        self, coordinators: dict[str, BydDataUpdateCoordinator]
    ) -> None:
        """Register telemetry coordinators for MQTT push dispatch."""
        self._coordinators = coordinators

    def _write_debug_dump(self, category: str, payload: dict[str, Any]) -> None:
        if not self._debug_dumps_enabled:
            return
        try:
            self._debug_dump_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%fZ")
            file_path = self._debug_dump_dir / f"{timestamp}_{category}.json"
            file_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Failed to write BYD debug dump.", exc_info=True)

    async def _async_write_debug_dump(
        self,
        category: str,
        payload: dict[str, Any],
    ) -> None:
        await self._hass.async_add_executor_job(
            self._write_debug_dump,
            category,
            payload,
        )

    def _handle_mqtt_event(
        self,
        event: str,
        vin: str,
        respond_data: dict[str, Any],
    ) -> None:
        """Handle every MQTT event from pyBYD.

        Single entry-point for all MQTT-pushed data.  Dispatches to
        event-specific helpers based on the *event* string:

        - ``vehicleInfo``   -> parse into model and push to coordinator.
        - ``remoteControl`` -> log acknowledgement and nudge coordinator.
        - (future events)   -> logged and debug-dumped automatically.
        """
        _LOGGER.debug(
            "MQTT event received: event=%s, vin=%s, keys=%s",
            event,
            vin[-6:] if vin else "-",
            list(respond_data.keys()) if respond_data else [],
        )

        # Debug dump every MQTT event.
        if self._debug_dumps_enabled:
            dump: dict[str, Any] = {
                "vin": vin,
                "mqtt_event": event,
                "respond_data": respond_data,
            }
            self._hass.async_create_task(
                self._async_write_debug_dump(f"mqtt_{event}", dump)
            )

        # Dispatch to event-specific handlers.
        if event == "vehicleInfo":
            self._handle_vehicle_info_event(vin, respond_data)
        elif event == "remoteControl":
            self._handle_remote_control_event(vin, respond_data)
        else:
            _LOGGER.debug(
                "Unhandled MQTT event type: event=%s, vin=%s",
                event,
                vin[-6:] if vin else "-",
            )

    def _handle_vehicle_info_event(
        self,
        vin: str,
        respond_data: dict[str, Any],
    ) -> None:
        """Parse a vehicleInfo MQTT push and dispatch to the coordinator."""
        coordinator = self._coordinators.get(vin)
        if coordinator is None:
            _LOGGER.debug(
                "MQTT vehicleInfo for unknown VIN: %s (known: %s)",
                vin[-6:],
                [v[-6:] for v in self._coordinators],
            )
            return
        try:
            data = VehicleRealtimeData.model_validate(respond_data)
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "Failed to parse MQTT vehicleInfo payload for VIN %s",
                vin[-6:],
                exc_info=True,
            )
            return
        _LOGGER.debug(
            "MQTT vehicleInfo push for VIN %s -- updating coordinator",
            vin[-6:],
        )
        coordinator.handle_mqtt_realtime(data)

    def _handle_remote_control_event(
        self,
        vin: str,
        respond_data: dict[str, Any],
    ) -> None:
        """Process an MQTT remoteControl acknowledgement."""
        serial = respond_data.get("requestSerial", "")
        _LOGGER.info(
            "MQTT remoteControl ack: vin=%s, serial=%s",
            vin[-6:] if vin else "-",
            serial,
        )
        coordinator = self._coordinators.get(vin)
        if coordinator is not None:
            coordinator.async_set_updated_data(coordinator.data)

    @property
    def config(self) -> BydConfig:
        return self._config

    async def _ensure_client(self) -> BydClient:
        """Return a ready-to-use client, creating one if needed.

        The client's own ``ensure_session()`` handles login and token
        expiry transparently -- we only manage the transport lifecycle.
        """
        if self._client is None:
            _LOGGER.debug(
                "Creating new pyBYD client: entry_id=%s",
                self._entry.entry_id,
            )
            self._client = BydClient(
                self._config,
                session=self._http_session,
                on_mqtt_event=self._handle_mqtt_event,
            )
            await self._client.__aenter__()
        return self._client

    async def _invalidate_client(self) -> None:
        """Tear down the current client so the next call creates a fresh one."""
        if self._client is not None:
            _LOGGER.debug(
                "Invalidating pyBYD client: entry_id=%s",
                self._entry.entry_id,
            )
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

        The pyBYD client handles login and session-expiry retries internally.
        This wrapper only maps pyBYD exceptions into Home Assistant
        ConfigEntry/Auth errors and recreates the transport on hard failures.
        """
        call_started = perf_counter()
        _LOGGER.debug(
            "BYD API call started: entry_id=%s, vin=%s, command=%s",
            self._entry.entry_id,
            vin[-6:] if vin else "-",
            command or "-",
        )
        try:
            client = await self._ensure_client()
            result = await handler(client)
            _LOGGER.debug(
                "BYD API call succeeded: entry_id=%s, vin=%s, command=%s, "
                "duration_ms=%.1f",
                self._entry.entry_id,
                vin[-6:] if vin else "-",
                command or "-",
                (perf_counter() - call_started) * 1000,
            )
            return result
        except BydSessionExpiredError:
            # Session invalidated elsewhere; reconnect and retry once.
            await self._invalidate_client()
            try:
                client = await self._ensure_client()
                return await handler(client)
            except (BydSessionExpiredError, BydAuthenticationError) as retry_exc:
                raise ConfigEntryAuthFailed(str(retry_exc)) from retry_exc
            except (BydApiError, BydTransportError) as retry_exc:
                raise UpdateFailed(str(retry_exc)) from retry_exc
            except Exception as retry_exc:  # noqa: BLE001
                raise UpdateFailed(str(retry_exc)) from retry_exc
        except BydControlPasswordError as exc:
            raise UpdateFailed(
                "Control PIN rejected or cloud control temporarily locked"
            ) from exc
        except BydRateLimitError as exc:
            raise UpdateFailed(
                "Command rate limited by BYD cloud, please retry shortly"
            ) from exc
        except BydEndpointNotSupportedError as exc:
            raise UpdateFailed("Feature not supported for this vehicle/region") from exc
        except BydTransportError as exc:
            # Hard transport error -- tear down so next call reconnects
            await self._invalidate_client()
            raise UpdateFailed(str(exc)) from exc
        except BydAuthenticationError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except BydApiError as exc:
            raise UpdateFailed(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug(
                "BYD API call failed: entry_id=%s, vin=%s, command=%s, "
                "duration_ms=%.1f, error=%s",
                self._entry.entry_id,
                vin[-6:] if vin else "-",
                command or "-",
                (perf_counter() - call_started) * 1000,
                type(exc).__name__,
            )
            raise


class BydDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for telemetry updates for a single VIN."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: BydApi,
        vehicle: Vehicle,
        vin: str,
        poll_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_telemetry_{vin[-6:]}",
            update_interval=timedelta(seconds=poll_interval),
        )
        self._api = api
        self._vehicle = vehicle
        self._vin = vin
        self._fixed_interval = timedelta(seconds=poll_interval)
        self._polling_enabled = True
        self._force_next_refresh = False
        # Local state tracking for conditional fetching.
        self._last_realtime: VehicleRealtimeData | None = None
        self._last_hvac: HvacStatus | None = None
        self._last_charging: ChargingStatus | None = None

    def handle_mqtt_realtime(self, data: VehicleRealtimeData) -> None:
        """Accept an MQTT-pushed realtime update and push to entities."""
        self._last_realtime = data
        if not isinstance(self.data, dict):
            return
        new_data = dict(self.data)
        new_data["realtime"] = {self._vin: data}
        self.async_set_updated_data(new_data)

    def _is_vehicle_on(self, realtime: VehicleRealtimeData | None) -> bool | None:
        if realtime is None:
            return None
        return realtime.is_vehicle_on

    def _should_fetch_hvac(self, realtime: VehicleRealtimeData | None) -> bool:
        # Always fetch once to establish initial HVAC state.
        if self._last_hvac is None:
            return True
        # Only poll HVAC while the vehicle is on.
        return self._is_vehicle_on(realtime) is True

    def _should_fetch_charging(self, realtime: VehicleRealtimeData | None) -> bool:
        # Always fetch once to establish initial charging state.
        if self._last_charging is None:
            return True
        if realtime is None:
            return True
        cs = getattr(realtime, "charging_state", None)
        if cs is not None:
            return cs != ChargingState.NOT_CHARGING
        # Fall back to cached charging model.
        return bool(
            getattr(self._last_charging, "is_connected", False)
            or getattr(self._last_charging, "is_charging", False)
        )

    async def _async_update_data(self) -> dict[str, Any]:
        _LOGGER.debug("Telemetry refresh started: vin=%s", self._vin[-6:])

        force = self._force_next_refresh
        self._force_next_refresh = False

        if not self._polling_enabled and not force:
            if isinstance(self.data, dict):
                return self.data
            return {"vehicles": {self._vin: self._vehicle}}

        async def _fetch(client: BydClient) -> dict[str, Any]:
            vehicle_map = {self._vin: self._vehicle}
            endpoint_failures: dict[str, str] = {}

            # --- Realtime (always) ---
            realtime: VehicleRealtimeData | None = None
            try:
                realtime = await client.get_vehicle_realtime(self._vin)
            except _AUTH_ERRORS:
                raise
            except _RECOVERABLE_ERRORS as exc:
                endpoint_failures["realtime"] = f"{type(exc).__name__}: {exc}"
                _LOGGER.warning(
                    "Realtime fetch failed: vin=%s, error=%s", self._vin, exc
                )

            # --- Energy (always) ---
            energy: EnergyConsumption | None = None
            try:
                energy = await client.get_energy_consumption(self._vin)
            except _AUTH_ERRORS:
                raise
            except _RECOVERABLE_ERRORS as exc:
                endpoint_failures["energy"] = f"{type(exc).__name__}: {exc}"
                _LOGGER.warning("Energy fetch failed: vin=%s, error=%s", self._vin, exc)

            # Use fresh realtime or fall back to previous cycle.
            realtime_gate = realtime or self._last_realtime

            # --- HVAC (conditional) ---
            hvac: HvacStatus | None = None
            if self._should_fetch_hvac(realtime_gate):
                try:
                    hvac = await client.get_hvac_status(self._vin)
                except _AUTH_ERRORS:
                    raise
                except _RECOVERABLE_ERRORS as exc:
                    endpoint_failures["hvac"] = f"{type(exc).__name__}: {exc}"
                    _LOGGER.warning(
                        "HVAC fetch failed: vin=%s, error=%s",
                        self._vin,
                        exc,
                    )
            else:
                _LOGGER.debug(
                    "HVAC fetch skipped: vin=%s, reason=vehicle_not_on",
                    self._vin[-6:],
                )

            # --- Charging (conditional) ---
            charging: ChargingStatus | None = None
            if self._should_fetch_charging(realtime_gate):
                try:
                    charging = await client.get_charging_status(self._vin)
                except _AUTH_ERRORS:
                    raise
                except _RECOVERABLE_ERRORS as exc:
                    endpoint_failures["charging"] = f"{type(exc).__name__}: {exc}"
                    _LOGGER.warning(
                        "Charging fetch failed: vin=%s, error=%s",
                        self._vin,
                        exc,
                    )
            else:
                _LOGGER.debug(
                    "Charging fetch skipped: vin=%s, reason=not_charging_or_unplugged",
                    self._vin[-6:],
                )

            # Update local state for next cycle's conditional decisions.
            if realtime is not None:
                self._last_realtime = realtime
            if hvac is not None:
                self._last_hvac = hvac
            if charging is not None:
                self._last_charging = charging

            # Build result maps, falling back to last-known data.
            realtime_map: dict[str, Any] = {}
            energy_map: dict[str, Any] = {}
            hvac_map: dict[str, Any] = {}
            charging_map: dict[str, Any] = {}

            effective_realtime = realtime or self._last_realtime
            if effective_realtime is not None:
                realtime_map[self._vin] = effective_realtime
            effective_energy = energy
            if effective_energy is not None:
                energy_map[self._vin] = effective_energy
            effective_hvac = hvac or self._last_hvac
            if effective_hvac is not None:
                hvac_map[self._vin] = effective_hvac
            effective_charging = charging or self._last_charging
            if effective_charging is not None:
                charging_map[self._vin] = effective_charging

            if self._vin not in realtime_map:
                raise UpdateFailed(
                    f"Realtime state unavailable for {self._vin}; "
                    "no data returned from API"
                )

            if endpoint_failures:
                _LOGGER.warning(
                    "Telemetry partial refresh: vin=%s, endpoint_failures=%s",
                    self._vin[-6:],
                    endpoint_failures,
                )

            # Debug dumps via model serialization.
            if self._api._debug_dumps_enabled:
                dump: dict[str, Any] = {"vin": self._vin, "sections": {}}
                if effective_realtime is not None:
                    dump["sections"]["realtime"] = effective_realtime.model_dump(
                        mode="json"
                    )
                if effective_energy is not None:
                    dump["sections"]["energy"] = effective_energy.model_dump(
                        mode="json"
                    )
                if effective_hvac is not None:
                    dump["sections"]["hvac"] = effective_hvac.model_dump(mode="json")
                if effective_charging is not None:
                    dump["sections"]["charging"] = effective_charging.model_dump(
                        mode="json"
                    )
                self._api._hass.async_create_task(
                    self._api._async_write_debug_dump("telemetry", dump)
                )

            return {
                "vehicles": vehicle_map,
                "realtime": realtime_map,
                "energy": energy_map,
                "hvac": hvac_map,
                "charging": charging_map,
            }

        data = await self._api.async_call(_fetch)
        _LOGGER.debug(
            "Telemetry refresh succeeded: vin=%s, realtime=%s, "
            "energy=%s, hvac=%s, charging=%s",
            self._vin[-6:],
            self._vin in data.get("realtime", {}),
            self._vin in data.get("energy", {}),
            self._vin in data.get("hvac", {}),
            self._vin in data.get("charging", {}),
        )
        return data

    @property
    def polling_enabled(self) -> bool:
        return self._polling_enabled

    def set_polling_enabled(self, enabled: bool) -> None:
        self._polling_enabled = bool(enabled)
        self.update_interval = self._fixed_interval if self._polling_enabled else None

    async def async_force_refresh(self) -> None:
        self._force_next_refresh = True
        await self.async_request_refresh()

    async def async_fetch_realtime(self) -> None:
        """Force-fetch realtime data and merge into coordinator state."""

        async def _fetch(client: BydClient) -> VehicleRealtimeData:
            return await client.get_vehicle_realtime(self._vin)

        data: VehicleRealtimeData = await self._api.async_call(
            _fetch, vin=self._vin, command="fetch_realtime"
        )
        self._last_realtime = data
        if isinstance(self.data, dict):
            merged = dict(self.data)
            merged["realtime"] = {self._vin: data}
            self.async_set_updated_data(merged)

    async def async_fetch_hvac(self) -> None:
        """Force-fetch HVAC status and merge into coordinator state."""

        async def _fetch(client: BydClient) -> HvacStatus:
            return await client.get_hvac_status(self._vin)

        data: HvacStatus = await self._api.async_call(
            _fetch, vin=self._vin, command="fetch_hvac"
        )
        self._last_hvac = data
        if isinstance(self.data, dict):
            merged = dict(self.data)
            merged["hvac"] = {self._vin: data}
            self.async_set_updated_data(merged)

    async def async_fetch_charging(self) -> None:
        """Force-fetch charging status and merge into coordinator state."""

        async def _fetch(client: BydClient) -> ChargingStatus:
            return await client.get_charging_status(self._vin)

        data: ChargingStatus = await self._api.async_call(
            _fetch, vin=self._vin, command="fetch_charging"
        )
        self._last_charging = data
        if isinstance(self.data, dict):
            merged = dict(self.data)
            merged["charging"] = {self._vin: data}
            self.async_set_updated_data(merged)

    async def async_fetch_energy(self) -> None:
        """Force-fetch energy consumption and merge into coordinator."""

        async def _fetch(client: BydClient) -> EnergyConsumption:
            return await client.get_energy_consumption(self._vin)

        data: EnergyConsumption = await self._api.async_call(
            _fetch, vin=self._vin, command="fetch_energy"
        )
        if isinstance(self.data, dict):
            merged = dict(self.data)
            merged["energy"] = {self._vin: data}
            self.async_set_updated_data(merged)


class BydGpsUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for GPS updates for a single VIN."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: BydApi,
        vehicle: Vehicle,
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
        self._vehicle = vehicle
        self._vin = vin
        self._telemetry_coordinator = telemetry_coordinator
        self._smart_polling = bool(smart_polling)
        self._fixed_interval = timedelta(seconds=poll_interval)
        self._active_interval = timedelta(seconds=active_interval)
        self._inactive_interval = timedelta(seconds=inactive_interval)
        self._current_interval = self._fixed_interval
        self._polling_enabled = True
        self._force_next_refresh = False

    @property
    def polling_enabled(self) -> bool:
        return self._polling_enabled

    def set_polling_enabled(self, enabled: bool) -> None:
        self._polling_enabled = bool(enabled)
        self.update_interval = self._current_interval if self._polling_enabled else None

    async def async_force_refresh(self) -> None:
        self._force_next_refresh = True
        await self.async_request_refresh()

    async def async_fetch_gps(self) -> None:
        """Force-fetch GPS data and merge into coordinator state."""

        async def _fetch(client: BydClient) -> GpsInfo:
            return await client.get_gps_info(self._vin)

        data: GpsInfo = await self._api.async_call(
            _fetch, vin=self._vin, command="fetch_gps"
        )
        if isinstance(self.data, dict):
            merged = dict(self.data)
            merged["gps"] = {self._vin: data}
            self.async_set_updated_data(merged)

    def _is_vehicle_moving(self) -> bool:
        telemetry_data = (
            self._telemetry_coordinator.data if self._telemetry_coordinator else None
        )
        realtime_map = (
            telemetry_data.get("realtime", {})
            if isinstance(telemetry_data, dict)
            else {}
        )
        realtime = realtime_map.get(self._vin)
        speed = getattr(realtime, "speed", None) if realtime is not None else None
        if speed is None:
            gps = (
                self.data.get("gps", {}).get(self._vin)
                if isinstance(self.data, dict)
                else None
            )
            speed = getattr(gps, "speed", None) if gps is not None else None
        return bool(speed is not None and speed > 0)

    def _adjust_interval(self) -> None:
        if not self._smart_polling:
            self._current_interval = self._fixed_interval
        else:
            self._current_interval = (
                self._active_interval
                if self._is_vehicle_moving()
                else self._inactive_interval
            )
        if self._polling_enabled:
            self.update_interval = self._current_interval

    async def _async_update_data(self) -> dict[str, Any]:
        _LOGGER.debug("GPS refresh started: vin=%s", self._vin[-6:])

        force = self._force_next_refresh
        self._force_next_refresh = False

        if not self._polling_enabled and not force:
            if isinstance(self.data, dict):
                return self.data
            return {"vehicles": {self._vin: self._vehicle}}

        async def _fetch(client: BydClient) -> dict[str, Any]:
            vehicle_map = {self._vin: self._vehicle}

            gps: GpsInfo | None = None
            try:
                gps = await client.get_gps_info(self._vin)
            except _AUTH_ERRORS:
                raise
            except _RECOVERABLE_ERRORS as exc:
                _LOGGER.warning("GPS fetch failed: vin=%s, error=%s", self._vin, exc)

            gps_map: dict[str, Any] = {}
            if gps is not None:
                gps_map[self._vin] = gps

            if not gps_map:
                raise UpdateFailed(f"GPS fetch failed for {self._vin}")

            # Debug dump for GPS.
            if self._api._debug_dumps_enabled and gps is not None:
                dump = {
                    "vin": self._vin,
                    "sections": {"gps": gps.model_dump(mode="json")},
                }
                self._api._hass.async_create_task(
                    self._api._async_write_debug_dump("gps", dump)
                )

            return {
                "vehicles": vehicle_map,
                "gps": gps_map,
            }

        data = await self._api.async_call(_fetch)
        self._adjust_interval()
        _LOGGER.debug(
            "GPS refresh succeeded: vin=%s, gps=%s",
            self._vin[-6:],
            self._vin in data.get("gps", {}),
        )
        return data


def get_vehicle_display(vehicle: Vehicle) -> str:
    """Return a friendly name for a vehicle."""
    return vehicle.model_name or vehicle.vin
