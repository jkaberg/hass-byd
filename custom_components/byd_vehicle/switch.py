"""Switches for BYD Vehicle."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pybyd.models.hvac import HvacStatus

from .const import DOMAIN
from .coordinator import BydApi, BydDataUpdateCoordinator, get_vehicle_display
from .select import _gather_seat_climate_state


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BYD switches from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: BydDataUpdateCoordinator = data["coordinator"]
    api: BydApi = data["api"]

    entities: list[SwitchEntity] = []
    vehicle_map = coordinator.data.get("vehicles", {})

    for vin, vehicle in vehicle_map.items():
        entities.append(BydCarOnSwitch(coordinator, api, vin, vehicle))
        entities.append(BydBatteryHeatSwitch(coordinator, api, vin, vehicle))
        entities.append(BydSteeringWheelHeatSwitch(coordinator, api, vin, vehicle))

    async_add_entities(entities)


class BydBatteryHeatSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of the BYD battery heat toggle."""

    _attr_has_entity_name = True
    _attr_name = None
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
    def available(self) -> bool:
        """Available when coordinator has data for this vehicle."""
        if not super().available:
            return False
        if self._vin not in self.coordinator.data.get("vehicles", {}):
            return False
        return self._api.is_remote_command_supported(self._vin, "battery_heat_on")

    @property
    def is_on(self) -> bool | None:
        """Return whether battery heat is on."""
        realtime_map = self.coordinator.data.get("realtime", {})
        realtime = realtime_map.get(self._vin)
        if realtime is not None:
            val = getattr(realtime, "battery_heat_state", None)
            if val is not None:
                return bool(val)
        return self._last_state

    @property
    def assumed_state(self) -> bool:
        """Return True if we have no realtime data."""
        realtime_map = self.coordinator.data.get("realtime", {})
        realtime = realtime_map.get(self._vin)
        if realtime is not None:
            return getattr(realtime, "battery_heat_state", None) is None
        return True

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on battery heat."""

        async def _call(client: Any) -> Any:
            return await client.set_battery_heat(self._vin, on=True)

        try:
            self._last_state = True
            await self._api.async_call(_call, vin=self._vin, command="battery_heat_on")
        except Exception as exc:  # noqa: BLE001
            self._last_state = None
            raise HomeAssistantError(str(exc)) from exc
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off battery heat."""

        async def _call(client: Any) -> Any:
            return await client.set_battery_heat(self._vin, on=False)

        try:
            self._last_state = False
            await self._api.async_call(_call, vin=self._vin, command="battery_heat_off")
        except Exception as exc:  # noqa: BLE001
            self._last_state = None
            raise HomeAssistantError(str(exc)) from exc
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {"vin": self._vin}
        for cmd in ("battery_heat_on", "battery_heat_off"):
            last_result = self._api.get_last_remote_result(self._vin, cmd)
            if last_result:
                attrs["last_remote_result"] = last_result
                break
        return attrs

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this switch."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._vin)},
            name=get_vehicle_display(self._vehicle),
            manufacturer=getattr(self._vehicle, "brand_name", None) or "BYD",
            model=getattr(self._vehicle, "model_name", None),
        )


class BydCarOnSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of a BYD car-on switch via climate control."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "car_on"
    _attr_icon = "mdi:car"
    _TEMP_21C_SCALE = 7

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

    def _get_hvac_status(self) -> HvacStatus | None:
        hvac_map = self.coordinator.data.get("hvac", {})
        hvac = hvac_map.get(self._vin)
        return hvac if isinstance(hvac, HvacStatus) else None

    @property
    def available(self) -> bool:
        """Available when coordinator has data for this vehicle."""
        if not super().available:
            return False
        if self._vin not in self.coordinator.data.get("vehicles", {}):
            return False
        return self._api.is_remote_command_supported(self._vin, "car_on")

    @property
    def is_on(self) -> bool | None:
        """Return whether car-on (climate) is on."""
        hvac = self._get_hvac_status()
        if hvac is not None:
            return bool(hvac.is_ac_on)
        return self._last_state

    @property
    def assumed_state(self) -> bool:
        """Return True if HVAC state is unavailable."""
        return self._get_hvac_status() is None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on car-on (start climate at 21°C)."""

        async def _call(client: Any) -> Any:
            return await client.start_climate(
                self._vin, temperature=self._TEMP_21C_SCALE
            )

        try:
            self._last_state = True
            await self._api.async_call(_call, vin=self._vin, command="car_on")
        except Exception as exc:  # noqa: BLE001
            self._last_state = None
            raise HomeAssistantError(str(exc)) from exc
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off car-on (stop climate)."""

        async def _call(client: Any) -> Any:
            return await client.stop_climate(self._vin)

        try:
            self._last_state = False
            await self._api.async_call(_call, vin=self._vin, command="car_off")
        except Exception as exc:  # noqa: BLE001
            self._last_state = None
            raise HomeAssistantError(str(exc)) from exc
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {
            "vin": self._vin,
            "target_temperature_c": 21,
        }
        for cmd in ("car_on", "car_off"):
            last_result = self._api.get_last_remote_result(self._vin, cmd)
            if last_result:
                attrs["last_remote_result"] = last_result
                break
        return attrs

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this switch."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._vin)},
            name=get_vehicle_display(self._vehicle),
            manufacturer=getattr(self._vehicle, "brand_name", None) or "BYD",
            model=getattr(self._vehicle, "model_name", None),
        )


class BydSteeringWheelHeatSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of the BYD steering wheel heat toggle."""

    _attr_has_entity_name = True
    _attr_name = None
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

    def _get_hvac_status(self) -> HvacStatus | None:
        hvac_map = self.coordinator.data.get("hvac", {})
        hvac = hvac_map.get(self._vin)
        return hvac if isinstance(hvac, HvacStatus) else None

    def _get_realtime(self) -> Any | None:
        return self.coordinator.data.get("realtime", {}).get(self._vin)

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        if self._vin not in self.coordinator.data.get("vehicles", {}):
            return False
        return self._api.is_remote_command_supported(
            self._vin, "steering_wheel_heat_on"
        )

    @property
    def is_on(self) -> bool | None:
        hvac = self._get_hvac_status()
        if hvac is not None:
            val = hvac.steering_wheel_heat_state
            if val is not None:
                return bool(val)
        realtime = self._get_realtime()
        if realtime is not None:
            val = getattr(realtime, "steering_wheel_heat_state", None)
            if val is not None:
                return bool(val)
        return self._last_state

    @property
    def assumed_state(self) -> bool:
        hvac = self._get_hvac_status()
        if hvac is not None:
            return hvac.steering_wheel_heat_state is None
        realtime = self._get_realtime()
        if realtime is not None:
            return getattr(realtime, "steering_wheel_heat_state", None) is None
        return True

    async def _set_steering_wheel_heat(self, on: bool) -> None:
        """Send seat climate command with steering wheel heat toggled."""
        hvac = self._get_hvac_status()
        realtime = self._get_realtime()
        kwargs = _gather_seat_climate_state(hvac, realtime)
        kwargs["steering_wheel_heat"] = 1 if on else 0

        async def _call(client: Any) -> Any:
            return await client.set_seat_climate(self._vin, **kwargs)

        cmd = "steering_wheel_heat_on" if on else "steering_wheel_heat_off"
        try:
            self._last_state = on
            await self._api.async_call(_call, vin=self._vin, command=cmd)
        except Exception as exc:  # noqa: BLE001
            self._last_state = None
            raise HomeAssistantError(str(exc)) from exc
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on steering wheel heating."""
        await self._set_steering_wheel_heat(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off steering wheel heating."""
        await self._set_steering_wheel_heat(False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"vin": self._vin}
        for cmd in ("steering_wheel_heat_on", "steering_wheel_heat_off"):
            last_result = self._api.get_last_remote_result(self._vin, cmd)
            if last_result:
                attrs["last_remote_result"] = last_result
                break
        return attrs

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._vin)},
            name=get_vehicle_display(self._vehicle),
            manufacturer=getattr(self._vehicle, "brand_name", None) or "BYD",
            model=getattr(self._vehicle, "model_name", None),
        )
