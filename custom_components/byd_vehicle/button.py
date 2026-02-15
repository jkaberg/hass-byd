"""Buttons for BYD Vehicle remote commands."""

# Pylint (v4+) can mis-infer dataclass-generated __init__ signatures for entity
# descriptions, causing false-positive E1123 errors.
# pylint: disable=unexpected-keyword-arg

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pybyd import BydRemoteControlError

from .const import DOMAIN
from .coordinator import BydApi, BydDataUpdateCoordinator, get_vehicle_display

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class BydButtonDescription(ButtonEntityDescription):
    """Describe a BYD button."""

    method: str  # client method name to call


BUTTON_DESCRIPTIONS: tuple[BydButtonDescription, ...] = (
    BydButtonDescription(
        key="flash_lights",
        name="Flash lights",
        icon="mdi:car-light-high",
        method="flash_lights",
    ),
    BydButtonDescription(
        key="find_car",
        name="Find car",
        icon="mdi:car-search",
        method="find_car",
    ),
    BydButtonDescription(
        key="close_windows",
        name="Close windows",
        icon="mdi:window-closed",
        method="close_windows",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BYD buttons from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[str, BydDataUpdateCoordinator] = data["coordinators"]
    gps_coordinators = data.get("gps_coordinators", {})
    api: BydApi = data["api"]

    entities: list[ButtonEntity] = []
    for vin, coordinator in coordinators.items():
        gps_coordinator = gps_coordinators.get(vin)
        vehicle = coordinator.data.get("vehicles", {}).get(vin)
        if vehicle is None:
            continue

        entities.append(BydForcePollButton(coordinator, gps_coordinator, vin, vehicle))
        for description in BUTTON_DESCRIPTIONS:
            entities.append(BydButton(coordinator, api, vin, vehicle, description))

    async_add_entities(entities)


class BydButton(CoordinatorEntity[BydDataUpdateCoordinator], ButtonEntity):
    """Representation of a BYD remote command button."""

    _attr_has_entity_name = True
    entity_description: BydButtonDescription

    def __init__(
        self,
        coordinator: BydDataUpdateCoordinator,
        api: BydApi,
        vin: str,
        vehicle: Any,
        description: BydButtonDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_translation_key = description.key
        self._api = api
        self._vin = vin
        self._vehicle = vehicle
        self._attr_unique_id = f"{vin}_button_{description.key}"

    @property
    def available(self) -> bool:
        """Available when coordinator has data for this vehicle."""
        if not super().available:
            return False
        if self.coordinator.data.get("vehicles", {}).get(self._vin) is None:
            return False
        return True

    async def async_press(self) -> None:
        """Execute the remote command."""
        method_name = self.entity_description.method

        async def _call(client: Any) -> Any:
            method = getattr(client, method_name, None)
            if method is None:
                raise HomeAssistantError(f"Command {method_name} not available")
            return await method(self._vin)

        try:
            await self._api.async_call(_call, vin=self._vin, command=method_name)
        except BydRemoteControlError as exc:
            _LOGGER.warning(
                "Button command %s sent but cloud reported failure — "
                "assuming optimistic outcome: %s",
                method_name,
                exc,
            )
            return
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(str(exc)) from exc

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {"vin": self._vin}

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this button."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._vin)},
            name=get_vehicle_display(self._vehicle),
            manufacturer=getattr(self._vehicle, "brand_name", None) or "BYD",
            model=getattr(self._vehicle, "model_name", None),
            serial_number=self._vin,
            hw_version=getattr(self._vehicle, "tbox_version", None) or None,
        )


class BydForcePollButton(CoordinatorEntity[BydDataUpdateCoordinator], ButtonEntity):
    """Button that forces a coordinator refresh (telemetry + GPS)."""

    _attr_has_entity_name = True
    _attr_translation_key = "force_poll"
    _attr_icon = "mdi:sync"
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
        self._attr_unique_id = f"{vin}_button_force_poll"

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        return self.coordinator.data.get("vehicles", {}).get(self._vin) is not None

    async def async_press(self) -> None:
        try:
            await self.coordinator.async_force_refresh()
            gps = self._gps_coordinator
            if gps is not None:
                await gps.async_force_refresh()
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(str(exc)) from exc

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
