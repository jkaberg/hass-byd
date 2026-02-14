"""Vehicle image entity for BYD Vehicle."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BydDataUpdateCoordinator, get_vehicle_display


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BYD vehicle image from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[str, BydDataUpdateCoordinator] = data["coordinators"]

    entities: list[ImageEntity] = []
    for vin, coordinator in coordinators.items():
        vehicle = coordinator.data.get("vehicles", {}).get(vin)
        if vehicle is None:
            continue
        url = getattr(vehicle, "pic_main_url", None)
        if url:
            entities.append(BydVehicleImage(coordinator, vin, vehicle, url))

    async_add_entities(entities)


class BydVehicleImage(CoordinatorEntity, ImageEntity):
    """Representation of a BYD vehicle image."""

    _attr_has_entity_name = True
    _attr_name = "Vehicle image"
    _attr_content_type = "image/png"

    def __init__(
        self,
        coordinator: BydDataUpdateCoordinator,
        vin: str,
        vehicle: Any,
        image_url: str,
    ) -> None:
        """Initialize the image entity."""
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, coordinator.hass)
        self._vin = vin
        self._vehicle = vehicle
        self._image_url = image_url
        self._attr_unique_id = f"{vin}_vehicle_image"
        self._attr_image_url = image_url
        self._attr_image_last_updated = datetime.now()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._vin)},
            name=get_vehicle_display(self._vehicle),
            manufacturer=getattr(self._vehicle, "brand_name", None) or "BYD",
            model=getattr(self._vehicle, "model_name", None),
            serial_number=self._vin,
            hw_version=getattr(self._vehicle, "tbox_version", None) or None,
        )
