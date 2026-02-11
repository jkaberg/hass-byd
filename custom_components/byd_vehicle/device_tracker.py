"""Device tracker for BYD Vehicle."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SOURCE_TYPE_GPS, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BydGpsUpdateCoordinator, get_vehicle_display


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    gps_coordinator: BydGpsUpdateCoordinator = data["gps_coordinator"]

    entities: list[TrackerEntity] = []

    vehicle_map = gps_coordinator.data.get("vehicles", {})
    for vin, vehicle in vehicle_map.items():
        entities.append(BydDeviceTracker(gps_coordinator, vin, vehicle))

    async_add_entities(entities)


class BydDeviceTracker(CoordinatorEntity, TrackerEntity):
    """Representation of a BYD vehicle tracker."""

    def __init__(self, coordinator: BydGpsUpdateCoordinator, vin: str, vehicle: Any) -> None:
        super().__init__(coordinator)
        self._vin = vin
        self._vehicle = vehicle
        self._attr_unique_id = f"{vin}_tracker"
        self._attr_name = f"{get_vehicle_display(vehicle)} location"

    @property
    def latitude(self) -> float | None:
        gps = self.coordinator.data.get("gps", {}).get(self._vin)
        return getattr(gps, "latitude", None) if gps else None

    @property
    def longitude(self) -> float | None:
        gps = self.coordinator.data.get("gps", {}).get(self._vin)
        return getattr(gps, "longitude", None) if gps else None

    @property
    def source_type(self) -> str:
        return SOURCE_TYPE_GPS

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._vin)},
            name=get_vehicle_display(self._vehicle),
            manufacturer=self._vehicle.brand_name or "BYD",
            model=self._vehicle.model_name or None,
        )
