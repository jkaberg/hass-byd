"""Climate control for BYD Vehicle."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature
from homeassistant.components.climate.const import HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pybyd import BydRemoteControlError
from pybyd.models.hvac import HvacStatus

from .const import CONF_CLIMATE_DURATION, DEFAULT_CLIMATE_DURATION, DOMAIN
from .coordinator import BydApi, BydDataUpdateCoordinator, get_vehicle_display

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
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


class BydClimate(CoordinatorEntity[BydDataUpdateCoordinator], ClimateEntity):
    """Representation of BYD climate control."""

    _BYD_SCALE_MIN = 1
    _BYD_SCALE_MAX = 17
    _TEMP_MIN_C = 15
    _TEMP_MAX_C = 31
    _TEMP_OFFSET_C = 14
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
        self._climate_duration = climate_duration
        self._attr_unique_id = f"{vin}_climate"
        self._last_mode = HVACMode.OFF
        self._last_command: str | None = None
        self._pending_target_temp: float | None = None
        self._command_pending = False

    def _get_hvac_status(self) -> HvacStatus | None:
        hvac_map = self.coordinator.data.get("hvac", {})
        hvac = hvac_map.get(self._vin)
        if isinstance(hvac, HvacStatus):
            return hvac
        return None

    def _scale_to_celsius(self, scale: int | float) -> float:
        scale_int = int(round(scale))
        scale_int = max(self._BYD_SCALE_MIN, min(self._BYD_SCALE_MAX, scale_int))
        return float(scale_int + self._TEMP_OFFSET_C)

    def _celsius_to_scale(self, temp_c: float | int) -> int:
        scale = int(round(float(temp_c) - self._TEMP_OFFSET_C))
        return max(self._BYD_SCALE_MIN, min(self._BYD_SCALE_MAX, scale))

    def _preset_from_scale(self, scale: int | float | None) -> str | None:
        if scale is None:
            return None
        scale_int = int(round(scale))
        if scale_int == self._BYD_SCALE_MAX:
            return self._PRESET_MAX_HEAT
        if scale_int == self._BYD_SCALE_MIN:
            return self._PRESET_MAX_COOL
        return None

    def _valid_target_temp_c(self, temp_c: float | int | None) -> float | None:
        if temp_c is None:
            return None
        temp_value = float(temp_c)
        if self._TEMP_MIN_C <= temp_value <= self._TEMP_MAX_C:
            return temp_value
        return None

    def _valid_target_scale(self, scale: int | float | None) -> int | None:
        if scale is None:
            return None
        scale_value = int(round(scale))
        if self._BYD_SCALE_MIN <= scale_value <= self._BYD_SCALE_MAX:
            return scale_value
        return None

    @property
    def available(self) -> bool:
        """Available when coordinator has data for this vehicle."""
        if not super().available:
            return False
        if self._vin not in self.coordinator.data.get("vehicles", {}):
            return False
        return True

    @property
    def hvac_mode(self) -> HVACMode:
        # After a command, prefer optimistic state until coordinator refreshes
        if self._command_pending:
            return self._last_mode
        hvac = self._get_hvac_status()
        if hvac is not None:
            return HVACMode.HEAT_COOL if hvac.is_ac_on else HVACMode.OFF
        return self._last_mode

    @property
    def assumed_state(self) -> bool:
        return self._command_pending or self._get_hvac_status() is None

    @property
    def current_temperature(self) -> float | None:
        hvac = self._get_hvac_status()
        if hvac is not None and hvac.interior_temp_available:
            return hvac.temp_in_car
        # Fall back to realtime data
        realtime_map = self.coordinator.data.get("realtime", {})
        realtime = realtime_map.get(self._vin)
        if realtime is not None:
            temp = getattr(realtime, "temp_in_car", None)
            if temp is not None and temp != -129:
                return temp
        return None

    @property
    def target_temperature(self) -> float | None:
        if self._pending_target_temp is not None:
            return self._pending_target_temp
        hvac = self._get_hvac_status()
        if hvac is not None:
            # main_setting_temp_new is already in °C (precise value from API)
            temp_c = self._valid_target_temp_c(hvac.main_setting_temp_new)
            if temp_c is not None:
                return temp_c
            # main_setting_temp is a BYD scale value (1-17) that needs conversion
            scale = self._valid_target_scale(hvac.main_setting_temp)
            if scale is not None:
                return self._scale_to_celsius(scale)
        return self._DEFAULT_TEMP_C

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode (on/off)."""
        temp = (
            self._pending_target_temp or self.target_temperature or self._DEFAULT_TEMP_C
        )

        async def _call(client: Any) -> Any:
            if hvac_mode == HVACMode.OFF:
                return await client.stop_climate(self._vin)
            kwargs: dict[str, Any] = {}
            if temp is not None:
                kwargs["temperature"] = self._celsius_to_scale(temp)
            kwargs["time_span"] = self._climate_duration
            return await client.start_climate(self._vin, **kwargs)

        try:
            self._last_command = (
                "stop_climate" if hvac_mode == HVACMode.OFF else "start_climate"
            )
            await self._api.async_call(_call, vin=self._vin, command=self._last_command)
        except BydRemoteControlError as exc:
            _LOGGER.warning(
                "Climate %s command sent but cloud reported failure — "
                "updating state optimistically: %s",
                self._last_command,
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(str(exc)) from exc

        self._last_mode = hvac_mode
        self._command_pending = True
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        scale = self._celsius_to_scale(temp)
        self._pending_target_temp = self._scale_to_celsius(scale)

        # If climate is currently on, send the update immediately
        if self.hvac_mode != HVACMode.OFF:

            async def _call(client: Any) -> Any:
                return await client.start_climate(
                    self._vin, temperature=scale, time_span=self._climate_duration
                )

            try:
                self._last_command = "start_climate"
                await self._api.async_call(
                    _call, vin=self._vin, command=self._last_command
                )
            except BydRemoteControlError as exc:
                _LOGGER.warning(
                    "Climate temperature command sent but cloud reported "
                    "failure — updating state optimistically: %s",
                    exc,
                )
            except Exception as exc:  # noqa: BLE001
                raise HomeAssistantError(str(exc)) from exc

        self._command_pending = True
        self.async_write_ha_state()

    @property
    def preset_mode(self) -> str | None:
        hvac = self._get_hvac_status()
        if hvac is not None and hvac.is_ac_on:
            # main_setting_temp_new is °C — convert back to scale for preset check
            temp_c = self._valid_target_temp_c(hvac.main_setting_temp_new)
            if temp_c is not None:
                return self._preset_from_scale(self._celsius_to_scale(temp_c))
            scale = self._valid_target_scale(hvac.main_setting_temp)
            return self._preset_from_scale(scale)
        if self.hvac_mode != HVACMode.OFF and self._pending_target_temp is not None:
            scale = self._celsius_to_scale(self._pending_target_temp)
            return self._preset_from_scale(scale)
        return None

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode not in self._attr_preset_modes:
            raise HomeAssistantError(f"Unsupported preset mode: {preset_mode}")
        if preset_mode == self._PRESET_MAX_HEAT:
            scale = self._BYD_SCALE_MAX
        else:
            scale = self._BYD_SCALE_MIN
        self._pending_target_temp = self._scale_to_celsius(scale)

        async def _call(client: Any) -> Any:
            return await client.start_climate(
                self._vin, temperature=scale, time_span=self._climate_duration
            )

        try:
            self._last_command = "start_climate"
            await self._api.async_call(_call, vin=self._vin, command=self._last_command)
        except BydRemoteControlError as exc:
            _LOGGER.warning(
                "Climate preset command sent but cloud reported failure — "
                "updating state optimistically: %s",
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(str(exc)) from exc

        self._last_mode = HVACMode.HEAT_COOL
        self._command_pending = True
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        """Clear optimistic state when fresh data arrives from the coordinator."""
        self._command_pending = False
        self._pending_target_temp = None
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"vin": self._vin}
        hvac = self._get_hvac_status()
        if hvac is not None:
            # Temperatures
            attrs["exterior_temperature"] = hvac.temp_out_car
            # copilot_setting_temp_new is already in °C;
            # copilot_setting_temp is a BYD scale value (1-17)
            attrs["passenger_set_temperature"] = (
                hvac.copilot_setting_temp_new
                if hvac.copilot_setting_temp_new is not None
                else (
                    self._scale_to_celsius(hvac.copilot_setting_temp)
                    if hvac.copilot_setting_temp is not None
                    else None
                )
            )
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

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._vin)},
            name=get_vehicle_display(self._vehicle),
            manufacturer=getattr(self._vehicle, "brand_name", None) or "BYD",
            model=getattr(self._vehicle, "model_name", None),
            serial_number=self._vin,
            hw_version=getattr(self._vehicle, "tbox_version", None) or None,
        )
