"""Lock control for BYD Vehicle."""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pybyd.models.realtime import LockState

from .const import DOMAIN
from .coordinator import BydApi, BydDataUpdateCoordinator
from .entity import BydVehicleEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BYD lock entities from a config entry."""
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


class BydLock(BydVehicleEntity, LockEntity):
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
            if value is None or value == LockState.UNKNOWN:
                return None
            parsed.append(value == LockState.LOCKED)
        return parsed

    @property
    def is_locked(self) -> bool | None:
        """Return True if all doors are locked."""
        if self._command_pending:
            return self._last_locked
        parsed = self._get_realtime_locks()
        if parsed is not None:
            return all(parsed)
        return self._last_locked

    @property
    def assumed_state(self) -> bool:
        """Return True when lock state is assumed."""
        if self._command_pending:
            return True
        parsed = self._get_realtime_locks()
        return parsed is None

    async def async_lock(self, **_: Any) -> None:
        """Lock the vehicle."""

        async def _call(client: Any) -> Any:
            return await client.lock(self._vin)

        self._last_command = "lock"
        self._last_locked = True
        await self._execute_command(
            self._api,
            _call,
            command="lock",
            on_rollback=lambda: setattr(self, "_last_locked", None),
        )

    async def async_unlock(self, **_: Any) -> None:
        """Unlock the vehicle."""

        async def _call(client: Any) -> Any:
            return await client.unlock(self._vin)

        self._last_command = "unlock"
        self._last_locked = False
        await self._execute_command(
            self._api,
            _call,
            command="unlock",
            on_rollback=lambda: setattr(self, "_last_locked", None),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = {**super().extra_state_attributes}
        if self._last_command:
            attrs["last_remote_command"] = self._last_command
        return attrs
