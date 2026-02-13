"""Buttons for BYD Vehicle remote commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BydApi, BydDataUpdateCoordinator, get_vehicle_display


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
    api: BydApi = data["api"]

    entities: list[ButtonEntity] = []
    for vin, coordinator in coordinators.items():
        vehicle = coordinator.data.get("vehicles", {}).get(vin)
        if vehicle is None:
            continue
        for description in BUTTON_DESCRIPTIONS:
            entities.append(BydButton(coordinator, api, vin, vehicle, description))

    async_add_entities(entities)


class BydButton(CoordinatorEntity, ButtonEntity):
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
        self._attr_name = description.name
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
        return self._api.is_remote_command_supported(
            self._vin,
            self.entity_description.method,
        )

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
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(str(exc)) from exc

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {"vin": self._vin}
        last_result = self._api.get_last_remote_result(
            self._vin, self.entity_description.method
        )
        if last_result:
            attrs["last_remote_result"] = last_result
        return attrs

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
