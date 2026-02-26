"""Switches for BYD Vehicle."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from pybyd.models.control import (
    BatteryHeatParams,
    ClimateStartParams,
    SeatClimateParams,
)

from .const import DOMAIN
from .coordinator import BydApi, BydDataUpdateCoordinator
from .entity import BydVehicleEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BYD switches from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[str, BydDataUpdateCoordinator] = data["coordinators"]
    gps_coordinators = data.get("gps_coordinators", {})
    api: BydApi = data["api"]

    entities: list[SwitchEntity] = []
    for vin, coordinator in coordinators.items():
        gps_coordinator = gps_coordinators.get(vin)
        vehicle = coordinator.data.get("vehicles", {}).get(vin)
        if vehicle is None:
            continue
        entities.append(
            BydDisablePollingSwitch(coordinator, gps_coordinator, vin, vehicle)
        )
        entities.append(BydCarOnSwitch(coordinator, api, vin, vehicle))
        entities.append(BydBatteryHeatSwitch(coordinator, api, vin, vehicle))
        entities.append(BydSteeringWheelHeatSwitch(coordinator, api, vin, vehicle))

    async_add_entities(entities)


class BydBatteryHeatSwitch(BydVehicleEntity, SwitchEntity):
    """Representation of the BYD battery heat toggle."""

    _attr_has_entity_name = True
    _attr_translation_key = "battery_heat"
    _attr_icon = "mdi:heat-wave"

    def __init__(
        self,
        coordinator: BydDataUpdateCoordinator,
        api: BydApi,
        vin: str,
        vehicle: Any,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._api = api
        self._vin = vin
        self._vehicle = vehicle
        self._attr_unique_id = f"{vin}_switch_battery_heat"
        self._last_state: bool | None = None

    @property
    def is_on(self) -> bool | None:
        """Return whether battery heat is on."""
        if self._command_pending:
            return self._last_state
        realtime = self._get_realtime()
        if realtime is not None:
            heating = realtime.is_battery_heating
            if heating is not None:
                return heating
        return self._last_state

    @property
    def assumed_state(self) -> bool:
        """Return True if we have no realtime data."""
        realtime = self._get_realtime()
        if realtime is not None:
            return getattr(realtime, "battery_heat_state", None) is None
        return True

    def _is_command_confirmed(self) -> bool:
        """Return True when realtime data confirms the battery heat command."""
        if self._last_state is None:
            return True
        realtime = self._get_realtime()
        if realtime is None:
            return False
        heating = getattr(realtime, "is_battery_heating", None)
        if heating is None:
            return False
        return bool(heating) == bool(self._last_state)

    async def async_turn_on(self, **_kwargs: Any) -> None:
        """Turn on battery heat."""

        async def _call(client: Any) -> Any:
            return await client.set_battery_heat(
                self._vin, params=BatteryHeatParams(on=True)
            )

        self._last_state = True
        await self._execute_command(
            self._api,
            _call,
            command="battery_heat_on",
            on_rollback=lambda: setattr(self, "_last_state", None),
        )

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Turn off battery heat."""

        async def _call(client: Any) -> Any:
            return await client.set_battery_heat(
                self._vin, params=BatteryHeatParams(on=False)
            )

        self._last_state = False
        await self._execute_command(
            self._api,
            _call,
            command="battery_heat_off",
            on_rollback=lambda: setattr(self, "_last_state", None),
        )


class BydCarOnSwitch(BydVehicleEntity, SwitchEntity):
    """Representation of a BYD car-on switch via climate control."""

    _attr_has_entity_name = True
    _attr_translation_key = "car_on"
    _attr_icon = "mdi:car"
    _DEFAULT_TEMP_C = 21.0

    def __init__(
        self,
        coordinator: BydDataUpdateCoordinator,
        api: BydApi,
        vin: str,
        vehicle: Any,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._api = api
        self._vin = vin
        self._vehicle = vehicle
        self._attr_unique_id = f"{vin}_switch_car_on"
        self._last_state: bool | None = None

    @property
    def is_on(self) -> bool | None:
        """Return whether car-on (climate) is on."""
        if self._command_pending:
            return self._last_state
        hvac = self._get_hvac_status()
        if hvac is not None:
            ac_on = bool(hvac.is_ac_on)
            if not ac_on:
                return False
            # ac_on=True: trust unless vehicle is off AND no recent HVAC command
            # (guards against stale ac_on=True after natural vehicle shutdown).
            if not self._is_vehicle_on() and not self.coordinator.hvac_command_pending:
                return False
            return True
        if not self._is_vehicle_on():
            return False
        return self._last_state

    @property
    def assumed_state(self) -> bool:
        """Return True if HVAC state is unavailable."""
        return self._get_hvac_status() is None

    async def async_turn_on(self, **_kwargs: Any) -> None:
        """Turn on car-on (start climate at 21°C)."""

        async def _call(client: Any) -> Any:
            return await client.start_climate(
                self._vin,
                params=ClimateStartParams(
                    temperature=self._DEFAULT_TEMP_C, time_span=1
                ),
            )

        self._last_state = True
        await self._execute_command(
            self._api,
            _call,
            command="car_on",
            on_rollback=lambda: setattr(self, "_last_state", None),
        )
        # Optimistic coordinator-level HVAC update so that *all* entities
        # (climate, seats, etc.) see the new state immediately.
        self.coordinator.apply_optimistic_hvac(
            ac_on=True,
            target_temp=self._DEFAULT_TEMP_C,
        )
        # Schedule a delayed refresh so the BYD cloud has time to update.
        self._schedule_delayed_refresh()

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Turn off car-on (stop climate)."""

        async def _call(client: Any) -> Any:
            return await client.stop_climate(self._vin)

        self._last_state = False
        await self._execute_command(
            self._api,
            _call,
            command="car_off",
            on_rollback=lambda: setattr(self, "_last_state", None),
        )
        # Optimistic coordinator-level HVAC update: mark A/C off and
        # reset seat heat/vent (the BYD car turns them off with climate).
        self.coordinator.apply_optimistic_hvac(
            ac_on=False,
            reset_seats=True,
        )
        self._schedule_delayed_refresh()

    def _is_command_confirmed(self) -> bool:
        """Check whether HVAC data confirms the car-on/off command."""
        hvac = self._get_hvac_status()
        if hvac is None:
            return False
        if bool(hvac.is_ac_on) != bool(self._last_state):
            return False
        # For turn-on: wait for realtime to confirm vehicle is on before
        # clearing _command_pending (prevents premature confirmation from
        # the optimistic HVAC patch).
        if self._last_state and not self._is_vehicle_on():
            return False
        return True

    _DELAYED_REFRESH_SECONDS = 20

    def _schedule_delayed_refresh(self) -> None:
        """Schedule a coordinator refresh after a short delay."""

        async def _delayed() -> None:
            await asyncio.sleep(self._DELAYED_REFRESH_SECONDS)
            await self.coordinator.async_force_refresh()

        self.hass.async_create_task(_delayed())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {**super().extra_state_attributes, "target_temperature_c": 21}


class BydSteeringWheelHeatSwitch(BydVehicleEntity, SwitchEntity):
    """Representation of the BYD steering wheel heat toggle."""

    _attr_has_entity_name = True
    _attr_translation_key = "steering_wheel_heat"
    _attr_icon = "mdi:steering"

    def __init__(
        self,
        coordinator: BydDataUpdateCoordinator,
        api: BydApi,
        vin: str,
        vehicle: Any,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._api = api
        self._vin = vin
        self._vehicle = vehicle
        self._attr_unique_id = f"{vin}_switch_steering_wheel_heat"
        self._last_state: bool | None = None

    @property
    def is_on(self) -> bool | None:
        """Return whether steering wheel heating is on."""
        if self._command_pending:
            return self._last_state
        # Vehicle off → steering wheel heat cannot be running.
        if not self._is_vehicle_on():
            return False
        hvac = self._get_hvac_status()
        if hvac is not None:
            val = hvac.is_steering_wheel_heating
            if val is not None:
                return val
        realtime = self._get_realtime()
        if realtime is not None:
            val = realtime.is_steering_wheel_heating
            if val is not None:
                return val
        return self._last_state

    @property
    def assumed_state(self) -> bool:
        """Return True when the state is assumed."""
        hvac = self._get_hvac_status()
        if hvac is not None:
            return hvac.is_steering_wheel_heating is None
        realtime = self._get_realtime()
        if realtime is not None:
            return realtime.is_steering_wheel_heating is None
        return True

    def _is_command_confirmed(self) -> bool:
        """Return True when data confirms the steering wheel heat command."""
        if self._last_state is None:
            return True
        hvac = self._get_hvac_status()
        if hvac is not None:
            val = hvac.is_steering_wheel_heating
            if val is not None:
                return bool(val) == bool(self._last_state)
        realtime = self._get_realtime()
        if realtime is not None:
            val = realtime.is_steering_wheel_heating
            if val is not None:
                return bool(val) == bool(self._last_state)
        return False

    async def _set_steering_wheel_heat(self, on: bool) -> None:
        """Send seat climate command with steering wheel heat toggled."""
        hvac = self._get_hvac_status()
        realtime = self._get_realtime()
        params = SeatClimateParams.from_current_state(hvac, realtime).with_change(
            "steering_wheel_heat_state", 1 if on else 3
        )

        async def _call(client: Any) -> Any:
            return await client.set_seat_climate(self._vin, params=params)

        cmd = "steering_wheel_heat_on" if on else "steering_wheel_heat_off"
        self._last_state = on
        await self._execute_command(
            self._api,
            _call,
            command=cmd,
            on_rollback=lambda: setattr(self, "_last_state", None),
        )

    async def async_turn_on(self, **_kwargs: Any) -> None:
        """Turn on steering wheel heating."""
        await self._set_steering_wheel_heat(True)

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Turn off steering wheel heating."""
        await self._set_steering_wheel_heat(False)


class BydDisablePollingSwitch(BydVehicleEntity, RestoreEntity, SwitchEntity):
    """Per-vehicle switch to disable scheduled polling."""

    _attr_has_entity_name = True
    _attr_translation_key = "disable_polling"
    _attr_icon = "mdi:sync-off"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: BydDataUpdateCoordinator,
        gps_coordinator: Any,
        vin: str,
        vehicle: Any,
    ) -> None:
        super().__init__(coordinator)
        self._vin = vin
        self._vehicle = vehicle
        self._gps_coordinator = gps_coordinator
        self._attr_unique_id = f"{vin}_switch_disable_polling"
        self._disabled = False

    async def async_added_to_hass(self) -> None:
        """Restore last state on startup."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._disabled = last.state == "on"
        self._apply()

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        return self.coordinator.data.get("vehicles", {}).get(self._vin) is not None

    @property
    def is_on(self) -> bool:
        """Return True when polling is disabled."""
        return self._disabled

    def _apply(self) -> None:
        self.coordinator.set_polling_enabled(not self._disabled)
        gps = self._gps_coordinator
        if gps is not None:
            gps.set_polling_enabled(not self._disabled)
        self.async_write_ha_state()

    async def async_turn_on(self, **_kwargs: Any) -> None:
        """Disable polling."""
        self._disabled = True
        self._apply()

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Re-enable polling."""
        self._disabled = False
        self._apply()
