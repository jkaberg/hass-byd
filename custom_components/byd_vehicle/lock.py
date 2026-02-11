"""Lock control for BYD Vehicle."""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
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

    entities: list[LockEntity] = []

    vehicle_map = coordinator.data.get("vehicles", {})
    for vin, vehicle in vehicle_map.items():
        entities.append(BydLock(coordinator, api, vin, vehicle))

    async_add_entities(entities)


class BydLock(CoordinatorEntity, LockEntity):
    """Representation of BYD lock control."""

    _attr_assumed_state = True

    def __init__(self, coordinator: BydDataUpdateCoordinator, api: BydApi, vin: str, vehicle: Any) -> None:
        super().__init__(coordinator)
        self._api = api
        self._vin = vin
        self._vehicle = vehicle
        self._attr_unique_id = f"{vin}_lock"
        self._attr_name = f"{get_vehicle_display(vehicle)} lock"

    @property
    def is_locked(self) -> bool | None:
        return None

    async def async_lock(self, **_: Any) -> None:
        async def _call(client: Any) -> Any:
            return await client.lock(self._vin)

        try:
            await self._api.async_call(_call)
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(str(exc)) from exc

    async def async_unlock(self, **_: Any) -> None:
        async def _call(client: Any) -> Any:
            return await client.unlock(self._vin)

        try:
            await self._api.async_call(_call)
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(str(exc)) from exc

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._vin)},
            name=get_vehicle_display(self._vehicle),
            manufacturer=self._vehicle.brand_name or "BYD",
            model=self._vehicle.model_name or None,
        )
