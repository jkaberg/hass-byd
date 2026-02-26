"""Binary sensors for BYD Vehicle."""

# Pylint (v4+) can mis-infer dataclass-generated __init__ signatures for entity
# descriptions, causing false-positive E1123 errors.
# pylint: disable=unexpected-keyword-arg

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pybyd.models.realtime import (
    ChargingState,
    DoorOpenState,
    WindowState,
)

from .const import DOMAIN
from .coordinator import BydDataUpdateCoordinator
from .entity import BydVehicleEntity


@dataclass(frozen=True, kw_only=True)
class BydBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a BYD binary sensor."""

    source: str = "realtime"
    attr_key: str | None = None
    value_fn: Callable[[Any], bool | None] | None = None


def _attr_truthy(attr_name: str) -> Callable[[Any], bool | None]:
    """Return a value_fn that checks ``bool(getattr(obj, attr_name))``."""

    def _fn(obj: Any) -> bool | None:
        val = getattr(obj, attr_name, None)
        if val is None:
            return None
        return bool(val)

    return _fn


def _attr_equals(attr_name: str, target: Any) -> Callable[[Any], bool | None]:
    """Return a value_fn that checks ``getattr(obj, attr_name) == target``."""

    def _fn(obj: Any) -> bool | None:
        val = getattr(obj, attr_name, None)
        if val is None:
            return None
        return val == target

    return _fn


BINARY_SENSOR_DESCRIPTIONS: tuple[BydBinarySensorDescription, ...] = (
    # =================================
    # Aggregate states (enabled)
    # =================================
    BydBinarySensorDescription(
        key="is_online",
        source="realtime",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda r: r.is_online,
    ),
    BydBinarySensorDescription(
        key="is_charging",
        source="realtime",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda r: r.charging_state == ChargingState.CHARGING,
    ),
    BydBinarySensorDescription(
        key="is_charger_connected",
        source="realtime",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=lambda r: (
            r.charging_state in (ChargingState.CONNECTED, ChargingState.CHARGING)
        ),
    ),
    BydBinarySensorDescription(
        key="is_any_door_open",
        source="realtime",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda r: r.is_any_door_open,
    ),
    BydBinarySensorDescription(
        key="is_any_window_open",
        source="realtime",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda r: r.is_any_window_open,
    ),
    BydBinarySensorDescription(
        key="is_locked",
        source="realtime",
        device_class=BinarySensorDeviceClass.LOCK,
        # is_locked returns True when locked; for BinarySensorDeviceClass.LOCK,
        # is_on=True means "problem" (unlocked), so invert. None propagates as-is.
        value_fn=lambda r: None if (v := r.is_locked) is None else not v,
    ),
    BydBinarySensorDescription(
        key="sentry_status",
        source="realtime",
        icon="mdi:shield-car",
        value_fn=_attr_truthy("sentry_status"),
    ),
    # ====================================
    # Individual doors (disabled)
    # ====================================
    BydBinarySensorDescription(
        key="left_front_door",
        source="realtime",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=_attr_equals("left_front_door", DoorOpenState.OPEN),
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="right_front_door",
        source="realtime",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=_attr_equals("right_front_door", DoorOpenState.OPEN),
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="left_rear_door",
        source="realtime",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=_attr_equals("left_rear_door", DoorOpenState.OPEN),
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="right_rear_door",
        source="realtime",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=_attr_equals("right_rear_door", DoorOpenState.OPEN),
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="trunk_lid",
        source="realtime",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=_attr_equals("trunk_lid", DoorOpenState.OPEN),
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="sliding_door",
        source="realtime",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=_attr_equals("sliding_door", DoorOpenState.OPEN),
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="forehold",
        source="realtime",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=_attr_equals("forehold", DoorOpenState.OPEN),
        entity_registry_enabled_default=False,
    ),
    # ====================================
    # Individual windows (disabled)
    # ====================================
    BydBinarySensorDescription(
        key="left_front_window",
        source="realtime",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=_attr_equals("left_front_window", WindowState.OPEN),
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="right_front_window",
        source="realtime",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=_attr_equals("right_front_window", WindowState.OPEN),
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="left_rear_window",
        source="realtime",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=_attr_equals("left_rear_window", WindowState.OPEN),
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="right_rear_window",
        source="realtime",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=_attr_equals("right_rear_window", WindowState.OPEN),
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="skylight",
        source="realtime",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=_attr_equals("skylight", WindowState.OPEN),
        entity_registry_enabled_default=False,
    ),
    # ====================================
    # Other (disabled)
    # ====================================
    BydBinarySensorDescription(
        key="battery_heat_state",
        source="realtime",
        icon="mdi:heat-wave",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_attr_truthy("battery_heat_state"),
    ),
    BydBinarySensorDescription(
        key="charge_heat_state",
        source="realtime",
        icon="mdi:heat-wave",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_attr_truthy("charge_heat_state"),
    ),
    BydBinarySensorDescription(
        key="vehicle_state",
        source="realtime",
        device_class=BinarySensorDeviceClass.POWER,
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda r: r.is_vehicle_on,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BYD binary sensors from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[str, BydDataUpdateCoordinator] = data["coordinators"]

    entities: list[BinarySensorEntity] = []
    for vin, coordinator in coordinators.items():
        vehicle = coordinator.data.get("vehicles", {}).get(vin)
        if vehicle is None:
            continue
        for description in BINARY_SENSOR_DESCRIPTIONS:
            entities.append(BydBinarySensor(coordinator, vin, vehicle, description))

    async_add_entities(entities)


class BydBinarySensor(BydVehicleEntity, BinarySensorEntity):
    """Representation of a BYD vehicle binary sensor."""

    _attr_has_entity_name = True
    entity_description: BydBinarySensorDescription

    def __init__(
        self,
        coordinator: BydDataUpdateCoordinator,
        vin: str,
        vehicle: Any,
        description: BydBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_translation_key = description.key
        self._vin = vin
        self._vehicle = vehicle
        self._attr_unique_id = f"{vin}_{description.source}_{description.key}"
        self._last_is_on: bool | None = None

        # Auto-disable binary sensors that return no data on first fetch.
        if description.entity_registry_enabled_default is not False:
            if self._resolve_value() is None:
                self._attr_entity_registry_enabled_default = False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_source_obj(self, source: str = "") -> Any | None:
        """Return the model object for this sensor's source."""
        return super()._get_source_obj(source or self.entity_description.source)

    def _resolve_value(self) -> bool | None:
        """Extract the current value using the description's extraction logic."""
        obj = self._get_source_obj()
        if obj is None:
            return None
        if self.entity_description.value_fn is not None:
            return self.entity_description.value_fn(obj)
        attr = self.entity_description.attr_key or self.entity_description.key
        value = getattr(obj, attr, None)
        if value is None:
            return None
        return bool(value)

    # ------------------------------------------------------------------
    # Entity properties
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """Return True when the coordinator has data for this source."""
        return super().available and self._get_source_obj() is not None

    @property
    def is_on(self) -> bool | None:
        """Return the binary sensor state, preserving last known when unavailable."""
        value = self._resolve_value()
        if value is not None:
            return value
        return self._last_is_on

    def _handle_coordinator_update(self) -> None:
        """Track last known state, then run standard coordinator update."""
        value = self._resolve_value()
        if value is not None:
            self._last_is_on = value
        super()._handle_coordinator_update()
