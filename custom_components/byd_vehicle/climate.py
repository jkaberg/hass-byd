"""Climate control for BYD Vehicle."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BydApi, BydDataUpdateCoordinator, get_vehicle_display


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: BydDataUpdateCoordinator = data["coordinator"]
    api: BydApi = data["api"]

    entities: list[ClimateEntity] = []

    vehicle_map = coordinator.data.get("vehicles", {})
    for vin, vehicle in vehicle_map.items():
        entities.append(BydClimate(coordinator, api, vin, vehicle))

    async_add_entities(entities)


class BydClimate(CoordinatorEntity, ClimateEntity):
    """Representation of BYD climate control."""

    _attr_supported_features = 0
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT_COOL]
    _attr_assumed_state = True

    def __init__(self, coordinator: BydDataUpdateCoordinator, api: BydApi, vin: str, vehicle: Any) -> None:
        super().__init__(coordinator)
        self._api = api
        self._vin = vin
        self._vehicle = vehicle
        self._attr_unique_id = f"{vin}_climate"
        self._attr_name = f"{get_vehicle_display(vehicle)} climate"
        self._last_mode = HVACMode.OFF

    @property
    def hvac_mode(self) -> HVACMode:
        return self._last_mode

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        async def _call(client: Any) -> Any:
            if hvac_mode == HVACMode.OFF:
                return await client.stop_climate(self._vin)
            return await client.start_climate(self._vin)

        try:
            await self._api.async_call(_call)
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(str(exc)) from exc

        self._last_mode = hvac_mode
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._vin)},
            name=get_vehicle_display(self._vehicle),
            manufacturer=self._vehicle.brand_name or "BYD",
            model=self._vehicle.model_name or None,
        )
