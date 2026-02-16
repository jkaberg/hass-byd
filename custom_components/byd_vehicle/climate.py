"""Climate control for BYD Vehicle."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature
from homeassistant.components.climate.const import HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pybyd import minutes_to_time_span
from pybyd.models.control import ClimateStartParams

from .const import (
    CONF_CLIMATE_DURATION,
    DEFAULT_CLIMATE_DURATION,
    DOMAIN,
)
from .coordinator import BydApi, BydDataUpdateCoordinator
from .entity import BydVehicleEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BYD climate entities from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[str, BydDataUpdateCoordinator] = data["coordinators"]
    api: BydApi = data["api"]
    climate_duration = entry.options.get(
        CONF_CLIMATE_DURATION,
        DEFAULT_CLIMATE_DURATION,
    )

    entities: list[ClimateEntity] = []

    for vin, coordinator in coordinators.items():
        vehicle = coordinator.data.get("vehicles", {}).get(vin)
        if vehicle is None:
            continue
        entities.append(BydClimate(coordinator, api, vin, vehicle, climate_duration))

    async_add_entities(entities)


class BydClimate(BydVehicleEntity, ClimateEntity):
    """Representation of BYD climate control."""

    _TEMP_MIN_C = 15
    _TEMP_MAX_C = 31
    _PRESET_MAX_HEAT = "max_heat"
    _PRESET_MAX_COOL = "max_cool"
    _DEFAULT_TEMP_C = 21.0

    _attr_has_entity_name = True
    _attr_translation_key = "climate"
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT_COOL]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
    )
    _attr_min_temp = _TEMP_MIN_C
    _attr_max_temp = _TEMP_MAX_C
    _attr_target_temperature_step = 1
    _attr_preset_modes = [_PRESET_MAX_HEAT, _PRESET_MAX_COOL]

    def __init__(
        self,
        coordinator: BydDataUpdateCoordinator,
        api: BydApi,
        vin: str,
        vehicle: Any,
        climate_duration: int = DEFAULT_CLIMATE_DURATION,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._vin = vin
        self._vehicle = vehicle
        self._climate_duration_code = minutes_to_time_span(climate_duration)
        self._attr_unique_id = f"{vin}_climate"
        self._last_mode = HVACMode.OFF
        self._last_command: str | None = None
        self._pending_target_temp: float | None = None

    @staticmethod
    def _clamp_temp(temp_c: float | int | None) -> float | None:
        """Clamp a temperature to the valid range, or return None."""
        if temp_c is None:
            return None
        val = float(temp_c)
        if BydClimate._TEMP_MIN_C <= val <= BydClimate._TEMP_MAX_C:
            return val
        return None

    @staticmethod
    def _preset_from_temp(temp_c: float | None) -> str | None:
        """Return a preset name if the temperature matches a preset boundary."""
        if temp_c is None:
            return None
        rounded = round(temp_c)
        if rounded >= BydClimate._TEMP_MAX_C:
            return BydClimate._PRESET_MAX_HEAT
        if rounded <= BydClimate._TEMP_MIN_C:
            return BydClimate._PRESET_MAX_COOL
        return None

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode."""
        # After a command, prefer optimistic state until coordinator refreshes
        if self._command_pending:
            return self._last_mode
        # If the vehicle is off, HVAC cannot be running regardless of
        # cached data (defence-in-depth; coordinator already omits stale
        # HVAC, but guard here too).
        if not self._is_vehicle_on():
            return HVACMode.OFF
        hvac = self._get_hvac_status()
        if hvac is not None:
            return HVACMode.HEAT_COOL if hvac.is_ac_on else HVACMode.OFF
        return self._last_mode

    @property
    def assumed_state(self) -> bool:
        """Return True when state is assumed (command pending or no HVAC data)."""
        return self._command_pending or self._get_hvac_status() is None

    @property
    def current_temperature(self) -> float | None:
        """Return the current interior temperature."""
        hvac = self._get_hvac_status()
        if hvac is not None and hvac.interior_temp_available:
            return hvac.temp_in_car
        # Fall back to realtime data
        realtime_map = self.coordinator.data.get("realtime", {})
        realtime = realtime_map.get(self._vin)
        if realtime is not None:
            temp = getattr(realtime, "temp_in_car", None)
            if temp is not None:
                return temp
        return None

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        if self._pending_target_temp is not None:
            return self._pending_target_temp
        hvac = self._get_hvac_status()
        if hvac is not None:
            # main_setting_temp_new is already in °C (precise value from API)
            temp_c = self._clamp_temp(hvac.main_setting_temp_new)
            if temp_c is not None:
                return temp_c
        return self._DEFAULT_TEMP_C

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode (on/off)."""
        temp = (
            self._pending_target_temp or self.target_temperature or self._DEFAULT_TEMP_C
        )

        async def _call(client: Any) -> Any:
            if hvac_mode == HVACMode.OFF:
                return await client.stop_climate(self._vin)
            return await client.start_climate(
                self._vin,
                params=ClimateStartParams(
                    temperature=temp,
                    time_span=self._climate_duration_code,
                ),
            )

        self._last_command = (
            "stop_climate" if hvac_mode == HVACMode.OFF else "start_climate"
        )
        self._last_mode = hvac_mode
        await self._execute_command(self._api, _call, command=self._last_command)

        # Refresh coordinator so the car-on switch (and HVAC snapshot) updates quickly.
        await self.coordinator.async_force_refresh()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        clamped = max(self._TEMP_MIN_C, min(self._TEMP_MAX_C, float(temp)))
        self._pending_target_temp = clamped

        # If climate is currently on, send the update immediately
        if self.hvac_mode != HVACMode.OFF:

            async def _call(client: Any) -> Any:
                return await client.start_climate(
                    self._vin,
                    params=ClimateStartParams(
                        temperature=clamped,
                        time_span=self._climate_duration_code,
                    ),
                )

            self._last_command = "start_climate"
            await self._execute_command(self._api, _call, command=self._last_command)
            return

        self._command_pending = True
        self.async_write_ha_state()

    @property
    def preset_mode(self) -> str | None:
        """Return the active preset mode, if any."""
        hvac = self._get_hvac_status()
        if hvac is not None and hvac.is_ac_on:
            temp_c = self._clamp_temp(hvac.main_setting_temp_new)
            if temp_c is not None:
                return self._preset_from_temp(temp_c)
        if self.hvac_mode != HVACMode.OFF and self._pending_target_temp is not None:
            return self._preset_from_temp(self._pending_target_temp)
        return None

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Activate a preset mode."""
        if preset_mode not in self._attr_preset_modes:
            raise HomeAssistantError(f"Unsupported preset mode: {preset_mode}")
        temp_c = (
            float(self._TEMP_MAX_C)
            if preset_mode == self._PRESET_MAX_HEAT
            else float(self._TEMP_MIN_C)
        )
        self._pending_target_temp = temp_c

        async def _call(client: Any) -> Any:
            return await client.start_climate(
                self._vin,
                params=ClimateStartParams(
                    temperature=temp_c,
                    time_span=self._climate_duration_code,
                ),
            )

        self._last_command = "start_climate"
        self._last_mode = HVACMode.HEAT_COOL
        await self._execute_command(self._api, _call, command=self._last_command)

    def _handle_coordinator_update(self) -> None:
        """Clear optimistic state when fresh data arrives from the coordinator."""
        self._pending_target_temp = None
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional HVAC attributes."""
        attrs = {**super().extra_state_attributes}
        hvac = self._get_hvac_status()
        if hvac is not None:
            # Temperatures
            attrs["exterior_temperature"] = hvac.temp_out_car
            # copilot_setting_temp_new is already in °C;
            # copilot_setting_temp is a BYD scale value (1-17)
            attrs["passenger_set_temperature"] = hvac.copilot_setting_temp_new
            # Airflow
            attrs["fan_speed"] = hvac.wind_mode
            attrs["airflow_direction"] = hvac.wind_position
            attrs["recirculation"] = hvac.cycle_choice
            # Defrost / deicing
            attrs["front_defrost"] = hvac.front_defrost_status
            attrs["rear_defrost"] = hvac.electric_defrost_status
            attrs["wiper_heat"] = hvac.wiper_heat_status
            # Air quality
            attrs["pm25"] = hvac.pm
            attrs["pm25_exterior_state"] = hvac.pm25_state_out_car
            # Misc
            attrs["rapid_heating"] = hvac.rapid_increase_temp_state
            attrs["rapid_cooling"] = hvac.rapid_decrease_temp_state
        if self._last_command:
            attrs["last_remote_command"] = self._last_command
        return attrs
