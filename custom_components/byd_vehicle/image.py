"""Vehicle image entity for BYD Vehicle."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BydDataUpdateCoordinator, get_vehicle_display
from .entity import BydVehicleEntity


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


class BydVehicleImage(BydVehicleEntity, ImageEntity):
    """Representation of a BYD vehicle image."""

    _attr_has_entity_name = True
    _attr_translation_key = "vehicle_image"
    _attr_content_type = "image/png"

    def __init__(
        self,
        coordinator: BydDataUpdateCoordinator,
        vin: str,
        vehicle: Any,
        image_url: str,
    ) -> None:
        """Initialize the image entity."""
        BydVehicleEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, coordinator.hass)
        self._vin = vin
        self._vehicle = vehicle
        self._image_url = image_url
        self._attr_unique_id = f"{vin}_vehicle_image"
        self._attr_image_url = image_url
        self._attr_image_last_updated = datetime.now()
