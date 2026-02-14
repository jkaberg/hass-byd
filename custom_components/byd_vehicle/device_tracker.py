"""Device tracker for BYD Vehicle."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
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
    gps_coordinators: dict[str, BydGpsUpdateCoordinator] = data["gps_coordinators"]

    entities: list[TrackerEntity] = []

    for vin, gps_coordinator in gps_coordinators.items():
        vehicle = gps_coordinator.data.get("vehicles", {}).get(vin)
        if vehicle is None:
            continue
        entities.append(BydDeviceTracker(gps_coordinator, vin, vehicle))

    async_add_entities(entities)


class BydDeviceTracker(CoordinatorEntity[BydGpsUpdateCoordinator], TrackerEntity):
    """Representation of a BYD vehicle tracker."""

    _attr_has_entity_name = True
    _attr_translation_key = "location"

    def __init__(
        self, coordinator: BydGpsUpdateCoordinator, vin: str, vehicle: Any
    ) -> None:
        super().__init__(coordinator)
        self._vin = vin
        self._vehicle = vehicle
        self._attr_unique_id = f"{vin}_tracker"

    @property
    def available(self) -> bool:
        """Available when coordinator has GPS data."""
        if not super().available:
            return False
        return self.coordinator.data.get("gps", {}).get(self._vin) is not None

    @property
    def latitude(self) -> float | None:
        gps = self.coordinator.data.get("gps", {}).get(self._vin)
        return getattr(gps, "latitude", None) if gps else None

    @property
    def longitude(self) -> float | None:
        gps = self.coordinator.data.get("gps", {}).get(self._vin)
        return getattr(gps, "longitude", None) if gps else None

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        gps = self.coordinator.data.get("gps", {}).get(self._vin)
        return {
            "vin": self._vin,
            "gps_speed": getattr(gps, "speed", None) if gps else None,
            "gps_direction": getattr(gps, "direction", None) if gps else None,
            "gps_timestamp": getattr(gps, "gps_timestamp", None) if gps else None,
        }

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
