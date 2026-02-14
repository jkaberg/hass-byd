"""Lock control for BYD Vehicle."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pybyd import BydRemoteControlError
from pybyd.models.realtime import LockState

from .const import DOMAIN
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

    entities: list[LockEntity] = []

    for vin, coordinator in coordinators.items():
        vehicle = coordinator.data.get("vehicles", {}).get(vin)
        if vehicle is None:
            continue
        entities.append(BydLock(coordinator, api, vin, vehicle))

    async_add_entities(entities)


class BydLock(CoordinatorEntity[BydDataUpdateCoordinator], LockEntity):
    """Representation of BYD lock control."""

    _attr_has_entity_name = True
    _attr_translation_key = "lock"

    def __init__(
        self,
        coordinator: BydDataUpdateCoordinator,
        api: BydApi,
        vin: str,
        vehicle: Any,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._vin = vin
        self._vehicle = vehicle
        self._attr_unique_id = f"{vin}_lock"
        self._last_command: str | None = None
        self._last_locked: bool | None = None
        self._command_pending = False

    @property
    def available(self) -> bool:
        """Available when coordinator has data for this vehicle."""
        if not super().available:
            return False
        if self._vin not in self.coordinator.data.get("vehicles", {}):
            return False
        return True

    def _get_realtime_locks(self) -> list[bool] | None:
        realtime_map = self.coordinator.data.get("realtime", {})
        realtime = realtime_map.get(self._vin)
        if realtime is None:
            return None

        lock_values: list[LockState | None] = [
            getattr(realtime, "left_front_door_lock", None),
            getattr(realtime, "right_front_door_lock", None),
            getattr(realtime, "left_rear_door_lock", None),
            getattr(realtime, "right_rear_door_lock", None),
        ]
        parsed: list[bool] = []
        for value in lock_values:
            if value is None:
                return None
            parsed.append(value == LockState.LOCKED)
        return parsed

    @property
    def is_locked(self) -> bool | None:
        if self._command_pending:
            return self._last_locked
        parsed = self._get_realtime_locks()
        if parsed is not None:
            return all(parsed)
        return self._last_locked

    @property
    def assumed_state(self) -> bool:
        if self._command_pending:
            return True
        parsed = self._get_realtime_locks()
        return parsed is None

    async def async_lock(self, **_: Any) -> None:
        async def _call(client: Any) -> Any:
            return await client.lock(self._vin)

        try:
            self._last_command = "lock"
            self._last_locked = True
            await self._api.async_call(_call, vin=self._vin, command=self._last_command)
        except BydRemoteControlError as exc:
            _LOGGER.warning(
                "Lock command sent but cloud reported failure — "
                "updating state optimistically: %s",
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            self._last_locked = None
            raise HomeAssistantError(str(exc)) from exc
        self._command_pending = True
        self.async_write_ha_state()

    async def async_unlock(self, **_: Any) -> None:
        async def _call(client: Any) -> Any:
            return await client.unlock(self._vin)

        try:
            self._last_command = "unlock"
            self._last_locked = False
            await self._api.async_call(_call, vin=self._vin, command=self._last_command)
        except BydRemoteControlError as exc:
            _LOGGER.warning(
                "Unlock command sent but cloud reported failure — "
                "updating state optimistically: %s",
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            self._last_locked = None
            raise HomeAssistantError(str(exc)) from exc
        self._command_pending = True
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        """Clear optimistic state when fresh data arrives."""
        self._command_pending = False
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"vin": self._vin}
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
