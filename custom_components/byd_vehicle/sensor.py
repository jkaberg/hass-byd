"""Sensors for BYD Vehicle."""

# Pylint (v4+) can mis-infer dataclass-generated __init__ signatures for entity
# descriptions, causing false-positive E1123 errors.
# pylint: disable=unexpected-keyword-arg

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfLength,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pybyd.models.realtime import TirePressureUnit

from .const import (
    CONF_DISTANCE_UNIT_MODE,
    DEFAULT_DISTANCE_UNIT_MODE,
    DISTANCE_UNIT_MODE_FOLLOW_HA,
    DISTANCE_UNIT_MODE_IMPERIAL,
    DOMAIN,
)
from .coordinator import BydDataUpdateCoordinator
from .entity import BydVehicleEntity


def _normalize_epoch(value: Any) -> datetime | None:
    """Convert epoch-like values (sec/ms) or datetime to UTC datetime."""
    if value is None:
        return None
    # pyBYD already parses timestamps into datetime via BydTimestamp.
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    if ts > 1_000_000_000_000:
        ts = ts / 1000
    try:
        return datetime.fromtimestamp(ts, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


@dataclass(frozen=True, kw_only=True)
class BydSensorDescription(SensorEntityDescription):
    """Describe a BYD sensor."""

    source: str = "realtime"
    attr_key: str | None = None
    value_fn: Callable[[Any], Any] | None = None


def _round_int_attr(attr: str) -> Callable[[Any], int | None]:
    """Create a converter that rounds a numeric attribute to an integer."""

    def _convert(obj: Any) -> int | None:
        value = getattr(obj, attr, None)
        if value is None:
            return None
        return int(round(float(value)))

    return _convert


SENSOR_DESCRIPTIONS: tuple[BydSensorDescription, ...] = (
    # =============================================
    # Realtime: primary sensors (enabled by default)
    # =============================================
    BydSensorDescription(
        key="elec_percent",
        source="realtime",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BydSensorDescription(
        key="endurance_mileage",
        source="realtime",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:map-marker-distance",
        value_fn=_round_int_attr("endurance_mileage"),
    ),
    BydSensorDescription(
        key="total_mileage",
        source="realtime",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        value_fn=_round_int_attr("total_mileage"),
    ),
    BydSensorDescription(
        key="speed",
        source="realtime",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BydSensorDescription(
        key="temp_in_car",
        source="realtime",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda obj: (
            int(round(obj.temp_in_car)) if obj.temp_in_car is not None else None
        ),
    ),
    # Tire pressures – unit resolved dynamically from tire_press_unit;
    # kPa is the default because most BYD vehicles report tirePressUnit=3.
    BydSensorDescription(
        key="left_front_tire_pressure",
        source="realtime",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:car-tire-alert",
    ),
    BydSensorDescription(
        key="right_front_tire_pressure",
        source="realtime",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:car-tire-alert",
    ),
    BydSensorDescription(
        key="left_rear_tire_pressure",
        source="realtime",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:car-tire-alert",
    ),
    BydSensorDescription(
        key="right_rear_tire_pressure",
        source="realtime",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:car-tire-alert",
    ),
    # =============================================
    # HVAC: primary sensors (enabled by default)
    # =============================================
    BydSensorDescription(
        key="temp_out_car",
        source="hvac",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_round_int_attr("temp_out_car"),
    ),
    BydSensorDescription(
        key="pm",
        source="hvac",
        native_unit_of_measurement="µg/m³",
        device_class=SensorDeviceClass.PM25,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # ===========================================================
    # Realtime: disabled by default (diagnostic / secondary data)
    # ===========================================================
    # Alt battery / range fields
    BydSensorDescription(
        key="power_battery",
        source="realtime",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="ev_endurance",
        source="realtime",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_round_int_attr("ev_endurance"),
    ),
    BydSensorDescription(
        key="endurance_mileage_v2",
        source="realtime",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_round_int_attr("endurance_mileage_v2"),
    ),
    BydSensorDescription(
        key="total_mileage_v2",
        source="realtime",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_round_int_attr("total_mileage_v2"),
    ),
    # Driving
    BydSensorDescription(
        key="power_gear",
        source="realtime",
        icon="mdi:car-shift-pattern",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Charging detail from realtime
    BydSensorDescription(
        key="charging_state",
        source="realtime",
        icon="mdi:ev-station",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="charge_state",
        source="realtime",
        icon="mdi:ev-station",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="wait_status",
        source="realtime",
        icon="mdi:timer-sand",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="full_hour",
        source="realtime",
        icon="mdi:clock-outline",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="full_minute",
        source="realtime",
        icon="mdi:clock-outline",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="remaining_hours",
        source="realtime",
        icon="mdi:clock-outline",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="remaining_minutes",
        source="realtime",
        icon="mdi:clock-outline",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="booking_charge_state",
        source="realtime",
        icon="mdi:calendar-clock",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="booking_charging_hour",
        source="realtime",
        icon="mdi:calendar-clock",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="booking_charging_minute",
        source="realtime",
        icon="mdi:calendar-clock",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Tire status indicators
    BydSensorDescription(
        key="left_front_tire_status",
        source="realtime",
        icon="mdi:car-tire-alert",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="right_front_tire_status",
        source="realtime",
        icon="mdi:car-tire-alert",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="left_rear_tire_status",
        source="realtime",
        icon="mdi:car-tire-alert",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="right_rear_tire_status",
        source="realtime",
        icon="mdi:car-tire-alert",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="tirepressure_system",
        source="realtime",
        icon="mdi:car-tire-alert",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="rapid_tire_leak",
        source="realtime",
        icon="mdi:car-tire-alert",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Power / energy from realtime
    BydSensorDescription(
        key="total_power",
        source="realtime",
        icon="mdi:flash",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="nearest_energy_consumption",
        source="realtime",
        icon="mdi:lightning-bolt",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="recent_50km_energy",
        source="realtime",
        icon="mdi:lightning-bolt",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Fuel (hybrid vehicles)
    BydSensorDescription(
        key="oil_endurance",
        source="realtime",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        icon="mdi:gas-station",
        entity_registry_enabled_default=False,
    ),
    BydSensorDescription(
        key="oil_percent",
        source="realtime",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:gas-station",
        entity_registry_enabled_default=False,
    ),
    BydSensorDescription(
        key="total_oil",
        source="realtime",
        icon="mdi:gas-station",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # System indicators
    BydSensorDescription(
        key="engine_status",
        source="realtime",
        icon="mdi:engine",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="epb",
        source="realtime",
        icon="mdi:car-brake-parking",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="eps",
        source="realtime",
        icon="mdi:steering",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="esp",
        source="realtime",
        icon="mdi:car-traction-control",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="abs_warning",
        source="realtime",
        icon="mdi:car-brake-abs",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="svs",
        source="realtime",
        icon="mdi:car-wrench",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="srs",
        source="realtime",
        icon="mdi:airbag",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="ect",
        source="realtime",
        icon="mdi:coolant-temperature",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="ect_value",
        source="realtime",
        icon="mdi:coolant-temperature",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="pwr",
        source="realtime",
        icon="mdi:flash-alert",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="power_system",
        source="realtime",
        icon="mdi:flash",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="upgrade_status",
        source="realtime",
        icon="mdi:cellphone-arrow-down",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # =========================================
    # HVAC: standalone sensors (not climate)
    # =========================================
    BydSensorDescription(
        key="refrigerator_state",
        source="hvac",
        icon="mdi:fridge",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="refrigerator_door_state",
        source="hvac",
        icon="mdi:fridge",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # ==========================================
    # Last updated timestamp
    # ==========================================
    BydSensorDescription(
        key="last_updated",
        source="realtime",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="gps_last_updated",
        source="gps",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:crosshairs-gps",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BYD sensors from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[str, BydDataUpdateCoordinator] = data["coordinators"]
    gps_coordinators = data.get("gps_coordinators", {})

    entities: list[SensorEntity] = []
    for vin, coordinator in coordinators.items():
        vehicle = coordinator.data.get("vehicles", {}).get(vin)
        if vehicle is None:
            continue
        for description in SENSOR_DESCRIPTIONS:
            if description.key == "gps_last_updated":
                gps_coordinator = gps_coordinators.get(vin)
                if gps_coordinator is not None:
                    entities.append(
                        BydSensor(gps_coordinator, vin, vehicle, description)
                    )
                continue
            entities.append(BydSensor(coordinator, vin, vehicle, description))

    async_add_entities(entities)


_TIRE_PRESSURE_KEYS = {
    "left_front_tire_pressure",
    "right_front_tire_pressure",
    "left_rear_tire_pressure",
    "right_rear_tire_pressure",
}

_TIRE_UNIT_MAP = {
    TirePressureUnit.BAR: UnitOfPressure.BAR,
    TirePressureUnit.PSI: UnitOfPressure.PSI,
    TirePressureUnit.KPA: UnitOfPressure.KPA,
}
_KM_TO_MILES = 0.621371


class BydSensor(BydVehicleEntity, SensorEntity):
    """Representation of a BYD vehicle sensor."""

    _attr_has_entity_name = True
    entity_description: BydSensorDescription

    def __init__(
        self,
        coordinator: BydDataUpdateCoordinator,
        vin: str,
        vehicle: Any,
        description: BydSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_translation_key = description.key
        self._vin = vin
        self._vehicle = vehicle
        self._attr_unique_id = f"{vin}_{description.source}_{description.key}"

        # Auto-disable sensors that return no data on first fetch.
        # If the description already disables the entity we leave it alone.
        # Otherwise we probe the initial data: if the car didn't return a
        # usable value the sensor is disabled so it stays out of the way.
        if description.entity_registry_enabled_default is not False:
            if self._resolve_value() is None:
                self._attr_entity_registry_enabled_default = False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_source_obj(self, source: str = "") -> Any | None:
        """Return the model object for this sensor's source."""
        return super()._get_source_obj(source or self.entity_description.source)

    def _resolve_value(self) -> Any:
        """Extract the current value using the description's extraction logic."""
        if self.entity_description.key == "last_updated":
            realtime = self.coordinator.data.get("realtime", {}).get(self._vin)
            if realtime is None:
                return None
            return _normalize_epoch(getattr(realtime, "timestamp", None))
        if self.entity_description.key == "gps_last_updated":
            gps = self.coordinator.data.get("gps", {}).get(self._vin)
            return _normalize_epoch(getattr(gps, "gps_timestamp", None))
        obj = self._get_source_obj()
        if obj is None:
            return None
        if self.entity_description.value_fn is not None:
            return self.entity_description.value_fn(obj)
        attr = self.entity_description.attr_key or self.entity_description.key
        value = getattr(obj, attr, None)
        enum_value = getattr(value, "value", None)
        if isinstance(enum_value, int):
            return enum_value
        return value

    # ------------------------------------------------------------------
    # Entity properties
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """Return True when the coordinator has data for this source."""
        if self.entity_description.key in ("last_updated", "gps_last_updated"):
            return super().available and self._resolve_value() is not None
        return super().available and self._get_source_obj() is not None

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit; tire pressures resolve dynamically."""
        desc_unit = self.entity_description.native_unit_of_measurement
        if self._distance_units_mode() == DISTANCE_UNIT_MODE_IMPERIAL:
            if desc_unit == UnitOfLength.KILOMETERS:
                return UnitOfLength.MILES
            if desc_unit == UnitOfSpeed.KILOMETERS_PER_HOUR:
                return UnitOfSpeed.MILES_PER_HOUR
        if self.entity_description.key not in _TIRE_PRESSURE_KEYS:
            return desc_unit
        obj = self._get_source_obj()
        if obj is not None:
            api_unit = getattr(obj, "tire_press_unit", None)
            if api_unit is not None:
                return _TIRE_UNIT_MAP.get(api_unit, desc_unit)
        return desc_unit

    @property
    def device_class(self) -> SensorDeviceClass | None:
        """Avoid HA auto-converting speed/distance when imperial mode is forced."""
        base = self.entity_description.device_class
        if self._distance_units_mode() == DISTANCE_UNIT_MODE_IMPERIAL and base in (
            SensorDeviceClass.DISTANCE,
            SensorDeviceClass.SPEED,
        ):
            return None
        return base

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        value = self._resolve_value()
        if value is None:
            return None

        if self._distance_units_mode() != DISTANCE_UNIT_MODE_IMPERIAL:
            return value

        desc_unit = self.entity_description.native_unit_of_measurement
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return value

        if desc_unit == UnitOfLength.KILOMETERS:
            return round(numeric * _KM_TO_MILES, 1)
        if desc_unit == UnitOfSpeed.KILOMETERS_PER_HOUR:
            return round(numeric * _KM_TO_MILES, 1)
        return value

    def _distance_units_mode(self) -> str:
        """Resolve distance unit mode from entry options, with HA fallback."""
        options = getattr(getattr(self.coordinator, "config_entry", None), "options", {})
        configured = options.get(CONF_DISTANCE_UNIT_MODE, DEFAULT_DISTANCE_UNIT_MODE)
        if configured != DISTANCE_UNIT_MODE_FOLLOW_HA:
            return configured
        if self.hass.config.units.length_unit == UnitOfLength.MILES:
            return DISTANCE_UNIT_MODE_IMPERIAL
        return DEFAULT_DISTANCE_UNIT_MODE
