"""Data coordinators for BYD Vehicle."""

from __future__ import annotations

import asyncio
import dataclasses
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
    BydRemoteControlError,
    BydSessionExpiredError,
    BydTransportError,
    RemoteControlResult,
)
from pybyd.config import BydConfig, DeviceProfile
from pybyd.models.realtime import VehicleState
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
from .freshness import build_telemetry_material_snapshot, snapshot_digest

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
        # Canonical telemetry freshness keyed by VIN.
        # This advances only when material telemetry values change.
        self._telemetry_freshness: dict[str, datetime] = {}
        self._telemetry_last_received: dict[str, datetime] = {}
        self._telemetry_snapshot_hash: dict[str, str] = {}
        self._gps_freshness: dict[str, datetime] = {}
        self._debug_dump_dir = Path(hass.config.path(".storage/byd_vehicle_debug"))
        self._reload_scheduled = False
        # Serialize all BYD cloud calls so telemetry polls and remote
        # commands never overlap (BYD returns 6024 for concurrent ops).
        self._api_lock = asyncio.Lock()
        _LOGGER.debug(
            "Initialized BYD API wrapper for entry %s (region=%s, language=%s)",
            entry.entry_id,
            entry.data[CONF_BASE_URL],
            entry.data.get(CONF_LANGUAGE, DEFAULT_LANGUAGE),
        )

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

    def update_telemetry_freshness(
        self,
        vin: str,
        *,
        realtime: Any | None = None,
        hvac: Any | None = None,
        charging: Any | None = None,
        energy: Any | None = None,
    ) -> bool:
        """Advance canonical telemetry freshness when material data changed.

        Returns True only when the material snapshot digest changed.
        """
        snapshot = build_telemetry_material_snapshot(
            realtime=realtime,
            hvac=hvac,
            charging=charging,
            energy=energy,
        )
        digest = snapshot_digest(snapshot)
        if digest is None:
            return False

        previous = self._telemetry_snapshot_hash.get(vin)
        if digest == previous:
            return False

        observed_candidates = [
            (
                _normalize_epoch(getattr(realtime, "timestamp", None))
                if realtime is not None
                else None
            ),
            (
                _normalize_epoch(getattr(charging, "update_time", None))
                if charging is not None
                else None
            ),
        ]
        observed = max(
            [candidate for candidate in observed_candidates if candidate is not None],
            default=datetime.now(tz=UTC),
        )

        self._telemetry_snapshot_hash[vin] = digest
        self._telemetry_freshness[vin] = observed
        return True

    def get_telemetry_freshness(self, vin: str) -> datetime | None:
        """Return canonical telemetry freshness timestamp for a VIN."""
        return self._telemetry_freshness.get(vin)

    def update_telemetry_last_received(
        self,
        vin: str,
        *,
        realtime: Any | None = None,
        charging: Any | None = None,
    ) -> None:
        """Store latest observed telemetry payload timestamp for a VIN."""
        observed_candidates = [
            (
                _normalize_epoch(getattr(realtime, "timestamp", None))
                if realtime is not None
                else None
            ),
            (
                _normalize_epoch(getattr(charging, "update_time", None))
                if charging is not None
                else None
            ),
        ]
        observed = max(
            [candidate for candidate in observed_candidates if candidate is not None],
            default=datetime.now(tz=UTC),
        )

        current = self._telemetry_last_received.get(vin)
        if current is None or observed > current:
            self._telemetry_last_received[vin] = observed

    def get_telemetry_last_received(self, vin: str) -> datetime | None:
        """Return latest telemetry payload timestamp for a VIN."""
        return self._telemetry_last_received.get(vin)

    def update_gps_freshness(self, vin: str, *, gps: Any | None = None) -> bool:
        """Advance GPS freshness timestamp from observed GPS payload."""
        if gps is None:
            return False
        observed = _normalize_epoch(getattr(gps, "gps_timestamp", None))
        if observed is None:
            observed = datetime.now(tz=UTC)
        current = self._gps_freshness.get(vin)
        if current is not None and observed <= current:
            return False
        self._gps_freshness[vin] = observed
        return True

    def get_gps_freshness(self, vin: str) -> datetime | None:
        """Return canonical GPS freshness timestamp for a VIN."""
        return self._gps_freshness.get(vin)

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

    def _record_transport_trace(self, payload: dict[str, Any]) -> None:
        safe_payload = self._json_safe(payload)
        self._hass.async_create_task(
            self._async_write_debug_dump("api_trace", safe_payload)
        )

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
            _LOGGER.debug(
                "Creating new pyBYD client for entry %s",
                self._entry.entry_id,
            )
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
            _LOGGER.debug(
                "Invalidating pyBYD client for entry %s",
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

        All calls are serialized through ``_api_lock`` so that
        telemetry polls and remote-control commands never overlap on
        BYD's cloud (which returns code 6024 for concurrent ops).

        The pybyd client handles login and session-expiry retries
        internally via ``ensure_session()``.  We only need to recreate
        the client on hard transport failures.
        """
        call_started = perf_counter()
        _LOGGER.debug(
            "BYD API call start (entry=%s, vin=%s, command=%s)",
            self._entry.entry_id,
            vin[-6:] if vin else "-",
            command or "-",
        )
        async with self._api_lock:
            try:
                result = await self._async_call_inner(handler, vin=vin, command=command)
                _LOGGER.debug(
                    "BYD API call success "
                    "(entry=%s, vin=%s, command=%s, duration_ms=%.1f)",
                    self._entry.entry_id,
                    vin[-6:] if vin else "-",
                    command or "-",
                    (perf_counter() - call_started) * 1000,
                )
                return result
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug(
                    "BYD API call failed "
                    "(entry=%s, vin=%s, command=%s, duration_ms=%.1f, error=%s)",
                    self._entry.entry_id,
                    vin[-6:] if vin else "-",
                    command or "-",
                    (perf_counter() - call_started) * 1000,
                    type(exc).__name__,
                )
                raise

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
        vehicle: Vehicle,
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
        self._vehicle = vehicle
        self._vin = vin
        self._active_interval = timedelta(seconds=active_interval)
        self._inactive_interval = timedelta(seconds=inactive_interval)
        self._current_interval = timedelta(seconds=poll_interval)

    def _desired_interval(self) -> timedelta:
        """Determine telemetry polling interval from freshness recency."""
        freshness = self._api.get_telemetry_freshness(self._vin)
        if freshness is None:
            return self._inactive_interval
        age = datetime.now(tz=UTC) - freshness
        if age <= self._active_interval:
            return self._active_interval
        return self._inactive_interval

    def get_telemetry_freshness(self) -> datetime | None:
        """Expose canonical telemetry freshness for sensors."""
        return self._api.get_telemetry_freshness(self._vin)

    def get_telemetry_last_received(self) -> datetime | None:
        """Expose telemetry last-received timestamp for sensors."""
        return self._api.get_telemetry_last_received(self._vin)

    def get_gps_freshness(self) -> datetime | None:
        """Expose canonical GPS freshness for sensors."""
        return self._api.get_gps_freshness(self._vin)

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

    def _is_due(self) -> bool:
        """Return True when telemetry poll should hit the cloud."""
        freshness = self._api.get_telemetry_freshness(self._vin)
        if freshness is None:
            return True
        age = datetime.now(tz=UTC) - freshness
        return age >= self._current_interval

    @staticmethod
    def _coerce_enum_int(value: Any) -> int | None:
        """Return integer value for enums/raw ints, else None."""
        if value is None:
            return None
        raw = getattr(value, "value", value)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _is_vehicle_on(self, realtime: Any | None) -> bool | None:
        """Return True when realtime state indicates vehicle is ON."""
        if realtime is None:
            return None
        state = getattr(realtime, "vehicle_state", None)
        if state is None:
            return None
        if not isinstance(state, VehicleState):
            return None

        on_state = getattr(VehicleState, "ON", None)
        if on_state is None:
            on_state = getattr(VehicleState, "STANDBY", None)
        if on_state is None:
            return None

        return state == on_state

    def _should_fetch_hvac_status(self, realtime: Any | None) -> bool:
        """Return True when HVAC status should be fetched.

        Always fetch once during startup to establish initial HVAC state.
        After that, fetch only while vehicle state is ON.
        """
        if not isinstance(self.data, dict):
            return True

        previous_hvac = self.data.get("hvac", {}).get(self._vin)
        if previous_hvac is None:
            return True

        return self._is_vehicle_on(realtime) is True

    def _should_fetch_charging_status(self, realtime: Any | None) -> bool:
        """Return True when charging status should be fetched.

        Always fetch once during startup to establish initial charging state.
        After that, poll charging while vehicle is actively charging or plugged.
        If realtime is unavailable/unknown, allow fetch to avoid blind spots.
        """
        if not isinstance(self.data, dict):
            return True

        previous_charging = self.data.get("charging", {}).get(self._vin)
        if previous_charging is None:
            return True

        if realtime is None:
            return True

        if bool(getattr(realtime, "is_charging", False)):
            return True

        charge_states: list[int] = []
        for attr in ("charge_state", "charging_state"):
            state = self._coerce_enum_int(getattr(realtime, attr, None))
            if state is not None:
                charge_states.append(state)

        if charge_states:
            return any(state != -1 for state in charge_states)

        return bool(getattr(previous_charging, "is_connected", False))

    async def _async_update_data(self) -> dict[str, Any]:
        _LOGGER.debug("Telemetry refresh start for VIN %s", self._vin[-6:])
        self._adjust_interval()
        if not self._is_due() and isinstance(self.data, dict):
            freshness = self._api.get_telemetry_freshness(self._vin)
            age = (
                (datetime.now(tz=UTC) - freshness).total_seconds()
                if freshness is not None
                else None
            )
            _LOGGER.debug(
                "Telemetry refresh skipped for VIN %s (age_s=%s, interval_s=%s)",
                self._vin[-6:],
                round(age, 1) if age is not None else None,
                self._current_interval.total_seconds(),
            )
            return self.data

        async def _fetch(client: BydClient) -> dict[str, Any]:
            vehicle_map = {self._vin: self._vehicle}

            auth_errors = (BydAuthenticationError, BydSessionExpiredError)
            recoverable_errors = (
                BydApiError,
                BydTransportError,
                BydRateLimitError,
                BydEndpointNotSupportedError,
            )
            realtime = None
            energy = None
            hvac = None
            charging = None
            endpoint_failures: dict[str, str] = {}
            try:
                realtime = await client.get_vehicle_realtime(
                    self._vin,
                    stale_after=self._current_interval.total_seconds(),
                )
            except auth_errors:
                raise
            except recoverable_errors as exc:
                endpoint_failures["realtime"] = f"{type(exc).__name__}: {exc}"
                _LOGGER.warning("Realtime fetch failed for %s: %s", self._vin, exc)
            try:
                energy = await client.get_energy_consumption(self._vin)
            except auth_errors:
                raise
            except recoverable_errors as exc:
                endpoint_failures["energy"] = f"{type(exc).__name__}: {exc}"
                _LOGGER.warning("Energy fetch failed for %s: %s", self._vin, exc)
            if self._should_fetch_hvac_status(realtime):
                try:
                    hvac = await client.get_hvac_status(self._vin)
                except auth_errors:
                    raise
                except recoverable_errors as exc:
                    endpoint_failures["hvac"] = f"{type(exc).__name__}: {exc}"
                    _LOGGER.warning("HVAC fetch failed for %s: %s", self._vin, exc)
            else:
                _LOGGER.debug(
                    "HVAC fetch skipped for VIN %s (vehicle not ON)",
                    self._vin[-6:],
                )
            if self._should_fetch_charging_status(realtime):
                try:
                    charging = await client.get_charging_status(self._vin)
                except auth_errors:
                    raise
                except recoverable_errors as exc:
                    endpoint_failures["charging"] = f"{type(exc).__name__}: {exc}"
                    _LOGGER.warning("Charging fetch failed for %s: %s", self._vin, exc)
            else:
                _LOGGER.debug(
                    "Charging fetch skipped for VIN %s (not charging and not plugged)",
                    self._vin[-6:],
                )

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
            elif isinstance(self.data, dict):
                previous_hvac = self.data.get("hvac", {}).get(self._vin)
                if previous_hvac is not None:
                    hvac_map[self._vin] = previous_hvac
            if charging is not None:
                charging_map[self._vin] = charging
            elif isinstance(self.data, dict):
                previous_charging = self.data.get("charging", {}).get(self._vin)
                if previous_charging is not None:
                    charging_map[self._vin] = previous_charging

            if not any([realtime_map, energy_map, hvac_map, charging_map]):
                raise UpdateFailed(f"All telemetry fetches failed for {self._vin}")

            previous_realtime = None
            if isinstance(self.data, dict):
                previous_realtime = self.data.get("realtime", {}).get(self._vin)
            if realtime is None and previous_realtime is None:
                raise UpdateFailed(
                    f"Realtime fetch failed for {self._vin}; "
                    "no cached realtime data is available"
                )

            if endpoint_failures:
                _LOGGER.warning(
                    "Telemetry partial refresh for VIN %s with endpoint failures: %s",
                    self._vin[-6:],
                    endpoint_failures,
                )

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
        self._api.update_telemetry_last_received(
            self._vin,
            realtime=data.get("realtime", {}).get(self._vin),
            charging=data.get("charging", {}).get(self._vin),
        )
        freshness_updated = self._api.update_telemetry_freshness(
            self._vin,
            realtime=data.get("realtime", {}).get(self._vin),
            hvac=data.get("hvac", {}).get(self._vin),
            charging=data.get("charging", {}).get(self._vin),
            energy=data.get("energy", {}).get(self._vin),
        )
        self._adjust_interval()
        if freshness_updated:
            _LOGGER.debug("Telemetry freshness advanced for VIN %s", self._vin[-6:])
        _LOGGER.debug(
            "Telemetry refresh success for VIN %s "
            "(realtime=%s, energy=%s, hvac=%s, charging=%s)",
            self._vin[-6:],
            self._vin in data.get("realtime", {}),
            self._vin in data.get("energy", {}),
            self._vin in data.get("hvac", {}),
            self._vin in data.get("charging", {}),
        )
        return data


class BydGpsUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for GPS updates for a single VIN with adaptive polling."""

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
        self._smart_polling = smart_polling
        self._fixed_interval = timedelta(seconds=poll_interval)
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

    def _desired_interval(self, data: dict[str, Any] | None = None) -> timedelta:
        """Determine interval from smart mode state or fixed interval."""
        if not self._smart_polling:
            self._last_smart_state = None
            return self._fixed_interval

        probe_data = data if isinstance(data, dict) else {}
        if not probe_data:
            probe_data = self.data if isinstance(self.data, dict) else {}

        is_moving = self._is_vehicle_moving(probe_data)
        self._last_smart_state = is_moving
        return self._active_interval if is_moving else self._inactive_interval

    def _is_due(self) -> bool:
        """Return True when GPS poll should hit the cloud."""
        freshness = self._api.get_gps_freshness(self._vin)
        if freshness is None:
            return True
        age = datetime.now(tz=UTC) - freshness
        return age >= self._current_interval

    def _adjust_interval(self, data: dict[str, Any] | None = None) -> None:
        """Adjust polling interval from smart-mode movement or fixed mode."""
        new_interval = self._desired_interval(data)
        is_moving = bool(self._last_smart_state)

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

    async def _async_update_data(self) -> dict[str, Any]:
        _LOGGER.debug("GPS refresh start for VIN %s", self._vin[-6:])
        self._adjust_interval()
        if not self._is_due() and isinstance(self.data, dict):
            freshness = self._api.get_gps_freshness(self._vin)
            age = (
                (datetime.now(tz=UTC) - freshness).total_seconds()
                if freshness is not None
                else None
            )
            _LOGGER.debug(
                "GPS refresh skipped for VIN %s "
                "(age_s=%s, interval_s=%s, smart_polling=%s, moving=%s)",
                self._vin[-6:],
                round(age, 1) if age is not None else None,
                self._current_interval.total_seconds(),
                self._smart_polling,
                self._last_smart_state,
            )
            return self.data

        async def _fetch(client: BydClient) -> dict[str, Any]:
            vehicle_map = {self._vin: self._vehicle}

            auth_errors = (BydAuthenticationError, BydSessionExpiredError)
            recoverable_errors = (
                BydApiError,
                BydTransportError,
                BydRateLimitError,
                BydEndpointNotSupportedError,
            )
            gps = None
            try:
                gps = await client.get_gps_info(
                    self._vin,
                    stale_after=self._current_interval.total_seconds(),
                )
            except auth_errors:
                raise
            except recoverable_errors as exc:
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
        self._api.update_gps_freshness(
            self._vin,
            gps=data.get("gps", {}).get(self._vin),
        )
        self._adjust_interval(data)
        _LOGGER.debug(
            "GPS refresh success for VIN %s (gps=%s, smart_polling=%s, moving=%s)",
            self._vin[-6:],
            self._vin in data.get("gps", {}),
            self._smart_polling,
            self._last_smart_state,
        )
        return data


def get_vehicle_display(vehicle: Vehicle) -> str:
    """Return a friendly name for a vehicle."""
    return _get_vehicle_name(vehicle)
