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
from pybyd.models.realtime import LockState

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

    _attr_has_entity_name = True
    _attr_name = "Lock"

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

    @property
    def available(self) -> bool:
        """Available when coordinator has data for this vehicle."""
        if not super().available:
            return False
        return self._vin in self.coordinator.data.get("vehicles", {})

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
        parsed = self._get_realtime_locks()
        if parsed is not None:
            return all(parsed)
        return self._last_locked

    @property
    def assumed_state(self) -> bool:
        parsed = self._get_realtime_locks()
        return parsed is None

    async def async_lock(self, **_: Any) -> None:
        async def _call(client: Any) -> Any:
            return await client.lock(self._vin)

        try:
            self._last_command = "lock"
            self._last_locked = True
            await self._api.async_call(_call, vin=self._vin, command=self._last_command)
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(str(exc)) from exc

    async def async_unlock(self, **_: Any) -> None:
        async def _call(client: Any) -> Any:
            return await client.unlock(self._vin)

        try:
            self._last_command = "unlock"
            self._last_locked = False
            await self._api.async_call(_call, vin=self._vin, command=self._last_command)
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(str(exc)) from exc

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"vin": self._vin}
        if self._last_command:
            attrs["last_remote_command"] = self._last_command
            last_result = self._api.get_last_remote_result(
                self._vin, self._last_command
            )
            if last_result:
                attrs["last_remote_result"] = last_result
        return attrs

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._vin)},
            name=get_vehicle_display(self._vehicle),
            manufacturer=getattr(self._vehicle, "brand_name", None) or "BYD",
            model=getattr(self._vehicle, "model_name", None),
        )
