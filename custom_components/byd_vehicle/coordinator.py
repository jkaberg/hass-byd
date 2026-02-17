"""Data coordinators for BYD Vehicle."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic, perf_counter
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
from pybyd.models.gps import GpsInfo
from pybyd.models.hvac import HvacOverallStatus, HvacStatus
from pybyd.models.realtime import (
    SeatHeatVentState,
    StearingWheelHeat,
    VehicleRealtimeData,
)
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

#: Seconds to wait after an MQTT remoteControl ack before fetching HVAC
#: state from the API.  Gives the BYD cloud time to propagate.
_MQTT_HVAC_FETCH_DELAY_S: float = 3.0

#: Maximum seconds to hold the optimistic HVAC guard.  During this
#: window, API responses that contradict the expected A/C state are
#: discarded so stale cloud data doesn't overwrite the optimistic patch.
#: The guard clears early when an API response *confirms* the expected state.
_OPTIMISTIC_HVAC_GUARD_TTL_S: float = 60.0

#: Seat / steering-wheel HVAC fields that are reset when climate is stopped.
_SEAT_HVAC_FIELDS: tuple[str, ...] = (
    "main_seat_heat_state",
    "main_seat_ventilation_state",
    "copilot_seat_heat_state",
    "copilot_seat_ventilation_state",
    "lr_seat_heat_state",
    "lr_seat_ventilation_state",
    "rr_seat_heat_state",
    "rr_seat_ventilation_state",
)
_STEERING_WHEEL_FIELD: str = "steering_wheel_heat_state"


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

    def _handle_vehicle_info(self, vin: str, data: VehicleRealtimeData) -> None:
        """Handle typed vehicleInfo push from pyBYD.

        pyBYD parses the raw MQTT payload into a ``VehicleRealtimeData``
        model and delivers it here — no additional parsing needed.
        """
        coordinator = self._coordinators.get(vin)
        if coordinator is None:
            _LOGGER.debug(
                "MQTT vehicleInfo for unknown VIN: %s (known: %s)",
                vin[-6:],
                [v[-6:] for v in self._coordinators],
            )
            return
        _LOGGER.debug(
            "MQTT vehicleInfo push for VIN %s -- updating coordinator",
            vin[-6:],
        )
        coordinator.handle_mqtt_realtime(data)

    def _handle_mqtt_event(
        self,
        event: str,
        vin: str,
        respond_data: dict[str, Any],
    ) -> None:
        """Handle generic MQTT events from pyBYD.

        Covers integration-level concerns that apply to *all* MQTT
        events: debug dumps, logging, and HA event-bus forwarding.

        ``vehicleInfo`` data dispatch is handled by
        ``_handle_vehicle_info`` (via pyBYD's ``on_vehicle_info``
        callback) which receives the already-parsed model — so we
        deliberately skip it here to avoid duplicate work.
        """

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

    def _handle_command_ack(
        self,
        event: str,
        vin: str,
        respond_data: dict[str, Any],
    ) -> None:
        """Process a genuine remote-control command ack from pyBYD.

        Called via pyBYD's ``on_command_ack`` callback which fires only
        for MQTT ``remoteControl`` events that are **not** correlated to
        an in-flight data poll (GPS, realtime).  This ensures GPS poll
        responses never trigger spurious data refreshes.

        After a command completes we schedule short-delay refreshes for
        **both** HVAC and realtime data.  Different command types need
        different data sources for confirmation:

        - Climate / seat / steering-wheel → HVAC
        - Lock / unlock / battery heat / close windows → Realtime

        Scheduling both is intentional: each call is a cheap HTTP POST
        (<1 s) and commands are user-initiated, infrequent events.
        """
        serial = respond_data.get("requestSerial", "")
        _LOGGER.debug(
            "Command ack: vin=%s, serial=%s",
            vin[-6:] if vin else "-",
            serial,
        )
        coordinator = self._coordinators.get(vin)
        if coordinator is not None:
            # Nudge entities so optimistic states are re-evaluated.
            coordinator.async_set_updated_data(coordinator.data)
            # Schedule data refreshes after a short grace period so the
            # BYD cloud has time to propagate the new state.
            self._hass.async_create_task(
                coordinator.async_fetch_hvac_delayed(_MQTT_HVAC_FETCH_DELAY_S)
            )
            self._hass.async_create_task(
                coordinator.async_fetch_realtime_delayed(_MQTT_HVAC_FETCH_DELAY_S)
            )

    @property
    def config(self) -> BydConfig:
        """Return the BYD client configuration."""
        return self._config

    @property
    def debug_dumps_enabled(self) -> bool:
        """Whether debug dumps are currently enabled."""
        return self._debug_dumps_enabled

    async def async_write_debug_dump(
        self,
        category: str,
        payload: dict[str, Any],
    ) -> None:
        """Write a debug dump file (public entry point for coordinators)."""
        await self._async_write_debug_dump(category, payload)

    async def async_shutdown(self) -> None:
        """Tear down the pyBYD client (for use during unload)."""
        await self._invalidate_client()

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
                on_vehicle_info=self._handle_vehicle_info,
                on_mqtt_event=self._handle_mqtt_event,
                on_command_ack=self._handle_command_ack,
            )
            await self._client.async_start()
        return self._client

    async def _invalidate_client(self) -> None:
        """Tear down the current client so the next call creates a fresh one."""
        if self._client is not None:
            _LOGGER.debug(
                "Invalidating pyBYD client: entry_id=%s",
                self._entry.entry_id,
            )
            try:
                await self._client.async_close()
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
        # Optimistic HVAC guard — prevents stale API data from
        # overwriting a recent optimistic patch.
        self._optimistic_hvac_until: float | None = None
        self._optimistic_ac_expected: bool | None = None

    def handle_mqtt_realtime(self, data: VehicleRealtimeData) -> None:
        """Accept an MQTT-pushed realtime update and push to entities."""
        self._last_realtime = data
        if not isinstance(self.data, dict):
            return
        new_data = dict(self.data)
        new_data["realtime"] = {self._vin: data}
        self.async_set_updated_data(new_data)

    @staticmethod
    def _is_vehicle_on(realtime: VehicleRealtimeData | None) -> bool | None:
        if realtime is None:
            return None
        return realtime.is_vehicle_on

    @property
    def is_vehicle_on(self) -> bool:
        """Whether the vehicle is currently powered on (based on last realtime)."""
        return self._is_vehicle_on(self._last_realtime) is True

    def _should_fetch_hvac(
        self, realtime: VehicleRealtimeData | None, *, force: bool = False
    ) -> bool:
        # Always fetch once to establish initial HVAC state.
        if self._last_hvac is None:
            return True
        # Always fetch when a force-refresh was requested (e.g. after a command).
        if force:
            return True
        # Only poll HVAC while the vehicle is on.
        return self._is_vehicle_on(realtime) is True

    def _accept_hvac_update(self, hvac: HvacStatus) -> bool:
        """Return ``True`` if *hvac* should replace current coordinator data.

        While the optimistic guard is active and the fetched data does
        **not** confirm the expected A/C state, the update is rejected
        so stale cloud data doesn't overwrite the optimistic patch.
        """
        if self._optimistic_hvac_until is None:
            return True
        if monotonic() >= self._optimistic_hvac_until:
            # Guard expired — accept whatever the API returns.
            _LOGGER.debug(
                "Optimistic HVAC guard expired for %s — accepting API data",
                self._vin[-6:],
            )
            self._optimistic_hvac_until = None
            self._optimistic_ac_expected = None
            return True
        if hvac.is_ac_on == self._optimistic_ac_expected:
            # API confirms the expected state — accept and clear guard.
            _LOGGER.debug(
                "Optimistic HVAC guard confirmed for %s — accepting API data",
                self._vin[-6:],
            )
            self._optimistic_hvac_until = None
            self._optimistic_ac_expected = None
            return True
        _LOGGER.debug(
            "Discarding stale HVAC data for %s (expected ac_on=%s, got ac_on=%s, "
            "guard active for %.0fs more)",
            self._vin[-6:],
            self._optimistic_ac_expected,
            hvac.is_ac_on,
            self._optimistic_hvac_until - monotonic(),
        )
        return False

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

            # Use fresh realtime or fall back to previous cycle.
            realtime_gate = realtime or self._last_realtime

            # --- HVAC (conditional) ---
            hvac: HvacStatus | None = None
            if self._should_fetch_hvac(realtime_gate, force=force):
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
                # Discard stale HVAC that contradicts the optimistic guard.
                if hvac is not None and not self._accept_hvac_update(hvac):
                    hvac = None
            else:
                _LOGGER.debug(
                    "HVAC fetch skipped: vin=%s, reason=vehicle_not_on",
                    self._vin[-6:],
                )

            # Update local state for next cycle's conditional decisions.
            if realtime is not None:
                self._last_realtime = realtime
            if hvac is not None:
                self._last_hvac = hvac

            # Build result maps, falling back to last-known data.
            realtime_map: dict[str, Any] = {}
            hvac_map: dict[str, Any] = {}

            effective_realtime = realtime or self._last_realtime
            if effective_realtime is not None:
                realtime_map[self._vin] = effective_realtime
            vehicle_on = self._is_vehicle_on(realtime or self._last_realtime)
            # Only fall back to cached HVAC when the vehicle is on;
            # stale HVAC data is meaningless once the vehicle turns off
            # (remote climate start also sets power_gear ON).
            effective_hvac = hvac or (self._last_hvac if vehicle_on else None)
            if effective_hvac is not None:
                hvac_map[self._vin] = effective_hvac

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
            if self._api.debug_dumps_enabled:
                dump: dict[str, Any] = {"vin": self._vin, "sections": {}}
                if effective_realtime is not None:
                    dump["sections"]["realtime"] = effective_realtime.model_dump(
                        mode="json"
                    )
                if effective_hvac is not None:
                    dump["sections"]["hvac"] = effective_hvac.model_dump(mode="json")
                self.hass.async_create_task(
                    self._api.async_write_debug_dump("telemetry", dump)
                )

            return {
                "vehicles": vehicle_map,
                "realtime": realtime_map,
                "hvac": hvac_map,
            }

        data = await self._api.async_call(_fetch)
        _LOGGER.debug(
            "Telemetry refresh succeeded: vin=%s, realtime=%s, hvac=%s",
            self._vin[-6:],
            self._vin in data.get("realtime", {}),
            self._vin in data.get("hvac", {}),
        )
        return data

    @property
    def polling_enabled(self) -> bool:
        """Whether scheduled polling is currently enabled."""
        return self._polling_enabled

    def set_polling_enabled(self, enabled: bool) -> None:
        """Enable or disable scheduled polling."""
        self._polling_enabled = bool(enabled)
        self.update_interval = self._fixed_interval if self._polling_enabled else None

    async def async_force_refresh(self) -> None:
        """Schedule an immediate data refresh."""
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
        if not self._accept_hvac_update(data):
            return
        self._last_hvac = data
        if isinstance(self.data, dict):
            merged = dict(self.data)
            merged["hvac"] = {self._vin: data}
            self.async_set_updated_data(merged)

    async def async_fetch_hvac_delayed(self, delay: float) -> None:
        """Wait *delay* seconds, then force-fetch HVAC and merge."""
        await asyncio.sleep(delay)
        try:
            await self.async_fetch_hvac()
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "Command-triggered HVAC fetch failed for %s — will retry at next poll",
                self._vin[-6:],
                exc_info=True,
            )

    async def async_fetch_realtime_delayed(self, delay: float) -> None:
        """Wait *delay* seconds, then force-fetch realtime and merge."""
        await asyncio.sleep(delay)
        try:
            await self.async_fetch_realtime()
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "Command-triggered realtime fetch failed "
                "for %s — will retry at next poll",
                self._vin[-6:],
                exc_info=True,
            )

    def apply_optimistic_hvac(
        self,
        *,
        ac_on: bool | None = None,
        target_temp: float | None = None,
        reset_seats: bool = False,
    ) -> None:
        """Patch the coordinator HVAC data optimistically and notify entities.

        This gives *all* listening entities (climate, A/C switch, seat
        selects, etc.) an immediate view of the expected post-command
        state without waiting for the next API poll.

        Parameters
        ----------
        ac_on:
            If not ``None``, sets ``status`` to ``HvacOverallStatus.ON``
            or ``HvacOverallStatus.OFF``.
        target_temp:
            If not ``None``, updates ``main_setting_temp_new`` (°C).
        reset_seats:
            If ``True``, sets all seat heat/ventilation fields to
            ``SeatHeatVentState.OFF`` and steering-wheel heat to
            ``StearingWheelHeat.OFF``.  Use when stopping climate,
            since the BYD car resets these.
        """
        if not isinstance(self.data, dict):
            return
        current_hvac: HvacStatus | None = self.data.get("hvac", {}).get(self._vin)
        if current_hvac is None:
            # No baseline HVAC data to patch — entities fall back to their
            # own per-entity optimistic state; the delayed refresh will
            # provide real data shortly.
            return

        updates: dict[str, Any] = {}
        if ac_on is not None:
            updates["status"] = HvacOverallStatus.ON if ac_on else HvacOverallStatus.OFF
        if target_temp is not None:
            updates["main_setting_temp_new"] = target_temp
        if reset_seats:
            for field in _SEAT_HVAC_FIELDS:
                # Only reset seats that were actually active.
                val = getattr(current_hvac, field, None)
                if val is not None and val not in (
                    SeatHeatVentState.OFF,
                    SeatHeatVentState.UNAVAILABLE,
                ):
                    updates[field] = SeatHeatVentState.OFF
            sw_val = getattr(current_hvac, _STEERING_WHEEL_FIELD, None)
            if sw_val is not None and sw_val != StearingWheelHeat.OFF:
                updates[_STEERING_WHEEL_FIELD] = StearingWheelHeat.OFF

        if not updates:
            return

        patched = current_hvac.model_copy(update=updates)
        self._last_hvac = patched
        merged = dict(self.data)
        merged["hvac"] = {self._vin: patched}
        self.async_set_updated_data(merged)
        # Arm the optimistic guard so stale API responses are discarded
        # until the cloud confirms the expected state.
        if ac_on is not None:
            self._optimistic_hvac_until = monotonic() + _OPTIMISTIC_HVAC_GUARD_TTL_S
            self._optimistic_ac_expected = ac_on
        guard = (
            f"ac_on={ac_on} for {_OPTIMISTIC_HVAC_GUARD_TTL_S}s"
            if ac_on is not None
            else "none"
        )
        _LOGGER.debug(
            "Optimistic HVAC update applied: " "vin=%s, updates=%s, guard=%s",
            self._vin[-6:],
            list(updates.keys()),
            guard,
        )


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
        """Whether scheduled GPS polling is currently enabled."""
        return self._polling_enabled

    def set_polling_enabled(self, enabled: bool) -> None:
        """Enable or disable scheduled GPS polling."""
        self._polling_enabled = bool(enabled)
        self.update_interval = self._current_interval if self._polling_enabled else None

    async def async_force_refresh(self) -> None:
        """Schedule an immediate GPS refresh."""
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

    def _adjust_interval(self) -> None:
        if not self._smart_polling:
            self._current_interval = self._fixed_interval
        else:
            self._current_interval = (
                self._active_interval
                if self._telemetry_coordinator is not None
                and self._telemetry_coordinator.is_vehicle_on
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
            if self._api.debug_dumps_enabled and gps is not None:
                dump = {
                    "vin": self._vin,
                    "sections": {"gps": gps.model_dump(mode="json")},
                }
                self.hass.async_create_task(
                    self._api.async_write_debug_dump("gps", dump)
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
