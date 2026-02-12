"""Binary sensors for BYD Vehicle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import (
    BydDataUpdateCoordinator,
    get_vehicle_display,
)


@dataclass(frozen=True, kw_only=True)
class BydBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a BYD binary sensor."""

    source: str = "realtime"
    attr_key: str | None = None
    value_fn: Callable[[Any], bool | None] | None = None


def _bool_from_int(attr: str) -> Callable[[Any], bool | None]:
    """Create a value function that converts int (0/non-zero) to bool."""

    def _fn(obj: Any) -> bool | None:
        val = getattr(obj, attr, None)
        if val is None:
            return None
        return bool(val)

    return _fn


BINARY_SENSOR_DESCRIPTIONS: tuple[BydBinarySensorDescription, ...] = (
    # =================================
    # Aggregate states (enabled)
    # =================================
    BydBinarySensorDescription(
        key="is_online",
        name="Online",
        source="realtime",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda r: r.is_online,
    ),
    BydBinarySensorDescription(
        key="is_charging",
        name="Charging",
        source="realtime",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda r: r.is_charging,
    ),
    BydBinarySensorDescription(
        key="is_any_door_open",
        name="Doors",
        source="realtime",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda r: r.is_any_door_open,
    ),
    BydBinarySensorDescription(
        key="is_any_window_open",
        name="Windows",
        source="realtime",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda r: r.is_any_window_open,
    ),
    BydBinarySensorDescription(
        key="is_locked",
        name="Locked",
        source="realtime",
        device_class=BinarySensorDeviceClass.LOCK,
        # is_locked returns True when locked; for BinarySensorDeviceClass.LOCK,
        # is_on=True means "problem" (unlocked), so invert
        value_fn=lambda r: not r.is_locked,
    ),
    BydBinarySensorDescription(
        key="charger_connected",
        name="Charger connected",
        source="charging",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=lambda c: c.is_connected,
    ),
    BydBinarySensorDescription(
        key="sentry_status",
        name="Sentry mode",
        source="realtime",
        icon="mdi:shield-car",
        value_fn=_bool_from_int("sentry_status"),
    ),
    # ====================================
    # Individual doors (disabled)
    # ====================================
    BydBinarySensorDescription(
        key="left_front_door",
        name="Front left door",
        source="realtime",
        device_class=BinarySensorDeviceClass.DOOR,
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="right_front_door",
        name="Front right door",
        source="realtime",
        device_class=BinarySensorDeviceClass.DOOR,
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="left_rear_door",
        name="Rear left door",
        source="realtime",
        device_class=BinarySensorDeviceClass.DOOR,
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="right_rear_door",
        name="Rear right door",
        source="realtime",
        device_class=BinarySensorDeviceClass.DOOR,
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="trunk_lid",
        name="Trunk",
        source="realtime",
        device_class=BinarySensorDeviceClass.DOOR,
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="sliding_door",
        name="Sliding door",
        source="realtime",
        device_class=BinarySensorDeviceClass.DOOR,
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="forehold",
        name="Frunk",
        source="realtime",
        device_class=BinarySensorDeviceClass.DOOR,
        entity_registry_enabled_default=False,
    ),
    # ====================================
    # Individual windows (disabled)
    # ====================================
    BydBinarySensorDescription(
        key="left_front_window",
        name="Front left window",
        source="realtime",
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="right_front_window",
        name="Front right window",
        source="realtime",
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="left_rear_window",
        name="Rear left window",
        source="realtime",
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="right_rear_window",
        name="Rear right window",
        source="realtime",
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="skylight",
        name="Skylight",
        source="realtime",
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_registry_enabled_default=False,
    ),
    # ====================================
    # Individual locks (disabled)
    # ====================================
    BydBinarySensorDescription(
        key="left_front_door_lock",
        name="Front left door lock",
        source="realtime",
        device_class=BinarySensorDeviceClass.LOCK,
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="right_front_door_lock",
        name="Front right door lock",
        source="realtime",
        device_class=BinarySensorDeviceClass.LOCK,
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="left_rear_door_lock",
        name="Rear left door lock",
        source="realtime",
        device_class=BinarySensorDeviceClass.LOCK,
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="right_rear_door_lock",
        name="Rear right door lock",
        source="realtime",
        device_class=BinarySensorDeviceClass.LOCK,
        entity_registry_enabled_default=False,
    ),
    BydBinarySensorDescription(
        key="sliding_door_lock",
        name="Sliding door lock",
        source="realtime",
        device_class=BinarySensorDeviceClass.LOCK,
        entity_registry_enabled_default=False,
    ),
    # ====================================
    # Other (disabled)
    # ====================================
    BydBinarySensorDescription(
        key="battery_heat_state",
        name="Battery heating",
        source="realtime",
        icon="mdi:heat-wave",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_bool_from_int("battery_heat_state"),
    ),
    BydBinarySensorDescription(
        key="charge_heat_state",
        name="Charge heating",
        source="realtime",
        icon="mdi:heat-wave",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_bool_from_int("charge_heat_state"),
    ),
    BydBinarySensorDescription(
        key="vehicle_state",
        name="Vehicle on",
        source="realtime",
        device_class=BinarySensorDeviceClass.POWER,
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda r: (
            bool(r.vehicle_state) if r.vehicle_state is not None else None
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BYD binary sensors from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: BydDataUpdateCoordinator = data["coordinator"]

    entities: list[BinarySensorEntity] = []
    vehicle_map = coordinator.data.get("vehicles", {})

    for vin, vehicle in vehicle_map.items():
        for description in BINARY_SENSOR_DESCRIPTIONS:
            entities.append(BydBinarySensor(coordinator, vin, vehicle, description))

    async_add_entities(entities)


class BydBinarySensor(CoordinatorEntity, BinarySensorEntity):
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
        self.entity_description = replace(
            description,
            name=None,
            translation_key=description.key,
        )
        self._vin = vin
        self._vehicle = vehicle
        self._attr_unique_id = f"{vin}_{description.source}_{description.key}"

        # Auto-disable binary sensors that return no data on first fetch.
        if description.entity_registry_enabled_default is not False:
            if self._resolve_value() is None:
                self._attr_entity_registry_enabled_default = False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_source_obj(self) -> Any | None:
        """Return the model object for this sensor's source."""
        source_map = self.coordinator.data.get(self.entity_description.source, {})
        return source_map.get(self._vin)

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
        """Return the binary sensor state."""
        return self._resolve_value()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this binary sensor."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._vin)},
            name=get_vehicle_display(self._vehicle),
            manufacturer=getattr(self._vehicle, "brand_name", None) or "BYD",
            model=getattr(self._vehicle, "model_name", None),
        )
