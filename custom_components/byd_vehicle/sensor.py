"""Sensors for BYD Vehicle."""

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
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pybyd.models.realtime import TirePressureUnit

from .const import DOMAIN
from .coordinator import (
    BydDataUpdateCoordinator,
    get_vehicle_display,
)


@dataclass(frozen=True, kw_only=True)
class BydSensorDescription(SensorEntityDescription):
    """Describe a BYD sensor."""

    source: str = "realtime"
    attr_key: str | None = None
    value_fn: Callable[[Any], Any] | None = None


def _filter_temp(obj: Any) -> float | None:
    """Filter out -129 sentinel temperature values."""
    val = getattr(obj, "temp_in_car", None)
    if val is None or val == -129:
        return None
    return float(val)


def _filter_string_attr(attr: str) -> Callable[[Any], str | None]:
    """Create a filter that removes '--' sentinel values for a named attribute."""

    def _filter(obj: Any) -> str | None:
        val = getattr(obj, attr, None)
        if val is None or val == "--":
            return None
        return str(val)

    return _filter


def _minutes_to_full(obj: Any) -> int | None:
    """Calculate minutes to full from hour/minute fields."""
    h = getattr(obj, "full_hour", None)
    m = getattr(obj, "full_minute", None)
    if h is None or m is None:
        return None
    h = max(int(h), 0)
    m = max(int(m), 0)
    return h * 60 + m


def _epoch_to_datetime(obj: Any) -> datetime | None:
    """Convert an epoch timestamp (seconds or milliseconds) to a datetime."""
    ts = getattr(obj, "timestamp", None)
    if ts is None:
        return None
    # BYD may return milliseconds; normalise to seconds.
    if ts > 1e12:
        ts = ts / 1000
    return datetime.fromtimestamp(ts, tz=UTC)


def _epoch_attr_to_datetime(attr: str) -> Callable[[Any], datetime | None]:
    """Create a converter for an epoch attr (seconds or milliseconds)."""

    def _convert(obj: Any) -> datetime | None:
        ts = getattr(obj, attr, None)
        if ts is None:
            return None
        ts_int = int(ts)
        if ts_int > 1_000_000_000_000:
            ts_int = int(ts_int / 1000)
        return datetime.fromtimestamp(ts_int, tz=UTC)

    return _convert


def _round_int_attr(attr: str) -> Callable[[Any], int | None]:
    """Create a converter that rounds a numeric attribute to an integer."""

    def _convert(obj: Any) -> int | None:
        value = getattr(obj, attr, None)
        if value is None:
            return None
        return int(round(float(value)))

    return _convert


def _charge_time_or_zero(attr: str) -> Callable[[Any], int | None]:
    """Create a converter that maps negative charge-time sentinels to zero."""

    def _convert(obj: Any) -> int | None:
        value = getattr(obj, attr, None)
        if value is None:
            return None
        parsed = int(value)
        if parsed < 0:
            return 0
        return parsed

    return _convert


SENSOR_DESCRIPTIONS: tuple[BydSensorDescription, ...] = (
    # =============================================
    # Realtime: primary sensors (enabled by default)
    # =============================================
    BydSensorDescription(
        key="elec_percent",
        name="Battery level",
        source="realtime",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BydSensorDescription(
        key="endurance_mileage",
        name="Range",
        source="realtime",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:map-marker-distance",
        value_fn=_round_int_attr("endurance_mileage"),
    ),
    BydSensorDescription(
        key="total_mileage",
        name="Odometer",
        source="realtime",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        value_fn=_round_int_attr("total_mileage"),
    ),
    BydSensorDescription(
        key="speed",
        name="Speed",
        source="realtime",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BydSensorDescription(
        key="temp_in_car",
        name="Cabin temperature",
        source="realtime",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda obj: (
            int(round(temp)) if (temp := _filter_temp(obj)) is not None else None
        ),
    ),
    # Tire pressures – unit resolved dynamically from tire_press_unit;
    # kPa is the default because most BYD vehicles report tirePressUnit=3.
    BydSensorDescription(
        key="left_front_tire_pressure",
        name="Front left tire pressure",
        source="realtime",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:car-tire-alert",
    ),
    BydSensorDescription(
        key="right_front_tire_pressure",
        name="Front right tire pressure",
        source="realtime",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:car-tire-alert",
    ),
    BydSensorDescription(
        key="left_rear_tire_pressure",
        name="Rear left tire pressure",
        source="realtime",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:car-tire-alert",
    ),
    BydSensorDescription(
        key="right_rear_tire_pressure",
        name="Rear right tire pressure",
        source="realtime",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:car-tire-alert",
    ),
    # ===============================================
    # Charging: primary sensors (enabled by default)
    # ===============================================
    BydSensorDescription(
        key="soc",
        name="Charging SOC",
        source="charging",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BydSensorDescription(
        key="time_to_full",
        name="Time to full charge",
        source="charging",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-clock",
        value_fn=_minutes_to_full,
    ),
    # =============================================
    # Energy: primary sensors (enabled by default)
    # =============================================
    BydSensorDescription(
        key="total_energy",
        name="Total energy consumption",
        source="energy",
        icon="mdi:lightning-bolt",
    ),
    BydSensorDescription(
        key="avg_energy_consumption",
        name="Average energy consumption",
        source="energy",
        icon="mdi:lightning-bolt",
    ),
    # =============================================
    # HVAC: primary sensors (enabled by default)
    # =============================================
    BydSensorDescription(
        key="temp_out_car",
        name="Exterior temperature",
        source="hvac",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_round_int_attr("temp_out_car"),
    ),
    BydSensorDescription(
        key="pm",
        name="PM2.5",
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
        name="Power battery level",
        source="realtime",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="ev_endurance",
        name="EV endurance",
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
        name="Range V2",
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
        name="Odometer V2",
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
        name="Gear position",
        source="realtime",
        icon="mdi:car-shift-pattern",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Charging detail from realtime
    BydSensorDescription(
        key="charging_state",
        name="Charging state",
        source="realtime",
        icon="mdi:ev-station",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="charge_state",
        name="Charge state",
        source="realtime",
        icon="mdi:ev-station",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="wait_status",
        name="Charge wait status",
        source="realtime",
        icon="mdi:timer-sand",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="full_hour",
        name="Hours to full",
        source="realtime",
        icon="mdi:clock-outline",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_charge_time_or_zero("full_hour"),
    ),
    BydSensorDescription(
        key="full_minute",
        name="Minutes to full",
        source="realtime",
        icon="mdi:clock-outline",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_charge_time_or_zero("full_minute"),
    ),
    BydSensorDescription(
        key="charge_remaining_hours",
        name="Charge remaining hours",
        source="realtime",
        icon="mdi:clock-outline",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_charge_time_or_zero("charge_remaining_hours"),
    ),
    BydSensorDescription(
        key="charge_remaining_minutes",
        name="Charge remaining minutes",
        source="realtime",
        icon="mdi:clock-outline",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_charge_time_or_zero("charge_remaining_minutes"),
    ),
    BydSensorDescription(
        key="booking_charge_state",
        name="Scheduled charging",
        source="realtime",
        icon="mdi:calendar-clock",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="booking_charging_hour",
        name="Scheduled charge hour",
        source="realtime",
        icon="mdi:calendar-clock",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="booking_charging_minute",
        name="Scheduled charge minute",
        source="realtime",
        icon="mdi:calendar-clock",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Tire status indicators
    BydSensorDescription(
        key="left_front_tire_status",
        name="Front left tire status",
        source="realtime",
        icon="mdi:car-tire-alert",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="right_front_tire_status",
        name="Front right tire status",
        source="realtime",
        icon="mdi:car-tire-alert",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="left_rear_tire_status",
        name="Rear left tire status",
        source="realtime",
        icon="mdi:car-tire-alert",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="right_rear_tire_status",
        name="Rear right tire status",
        source="realtime",
        icon="mdi:car-tire-alert",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="tirepressure_system",
        name="TPMS state",
        source="realtime",
        icon="mdi:car-tire-alert",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="rapid_tire_leak",
        name="Rapid tire leak",
        source="realtime",
        icon="mdi:car-tire-alert",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Power / energy from realtime
    BydSensorDescription(
        key="total_power",
        name="Total power",
        source="realtime",
        icon="mdi:flash",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="nearest_energy_consumption",
        name="Recent energy consumption",
        source="realtime",
        icon="mdi:lightning-bolt",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_filter_string_attr("nearest_energy_consumption"),
    ),
    BydSensorDescription(
        key="recent_50km_energy",
        name="Recent 50km energy",
        source="realtime",
        icon="mdi:lightning-bolt",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_filter_string_attr("recent_50km_energy"),
    ),
    # Fuel (hybrid vehicles)
    BydSensorDescription(
        key="oil_endurance",
        name="Fuel range",
        source="realtime",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        icon="mdi:gas-station",
        entity_registry_enabled_default=False,
    ),
    BydSensorDescription(
        key="oil_percent",
        name="Fuel level",
        source="realtime",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:gas-station",
        entity_registry_enabled_default=False,
    ),
    BydSensorDescription(
        key="total_oil",
        name="Total fuel consumption",
        source="realtime",
        icon="mdi:gas-station",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # System indicators
    BydSensorDescription(
        key="engine_status",
        name="Engine status",
        source="realtime",
        icon="mdi:engine",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="epb",
        name="Electronic parking brake",
        source="realtime",
        icon="mdi:car-brake-parking",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="eps",
        name="Electric power steering",
        source="realtime",
        icon="mdi:steering",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="esp",
        name="Electronic stability",
        source="realtime",
        icon="mdi:car-traction-control",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="abs_warning",
        name="ABS warning",
        source="realtime",
        icon="mdi:car-brake-abs",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="svs",
        name="Service vehicle soon",
        source="realtime",
        icon="mdi:car-wrench",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="srs",
        name="Airbag warning",
        source="realtime",
        icon="mdi:airbag",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="ect",
        name="Coolant temperature warning",
        source="realtime",
        icon="mdi:coolant-temperature",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="ect_value",
        name="Coolant temperature",
        source="realtime",
        icon="mdi:coolant-temperature",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="pwr",
        name="Power warning",
        source="realtime",
        icon="mdi:flash-alert",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="power_system",
        name="Power system",
        source="realtime",
        icon="mdi:flash",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="upgrade_status",
        name="OTA upgrade status",
        source="realtime",
        icon="mdi:cellphone-arrow-down",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # ============================================
    # Charging API: disabled by default (detail)
    # ============================================
    BydSensorDescription(
        key="charger_state",
        name="Charger state",
        source="charging",
        attr_key="charging_state",
        icon="mdi:ev-station",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="charger_connection",
        name="Charger connection state",
        source="charging",
        attr_key="connect_state",
        icon="mdi:ev-plug-type2",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="charging_update_time",
        name="Charging last update",
        source="charging",
        attr_key="update_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_epoch_attr_to_datetime("update_time"),
        icon="mdi:clock-outline",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # ==========================================
    # Energy API: disabled by default (detail)
    # ==========================================
    BydSensorDescription(
        key="electricity_consumption",
        name="Electricity consumption",
        source="energy",
        icon="mdi:lightning-bolt",
        entity_registry_enabled_default=False,
    ),
    BydSensorDescription(
        key="fuel_consumption",
        name="Fuel consumption",
        source="energy",
        icon="mdi:gas-station",
        entity_registry_enabled_default=False,
    ),
    # =========================================
    # HVAC: standalone sensors (not climate)
    # =========================================
    BydSensorDescription(
        key="refrigerator_state",
        name="Refrigerator",
        source="hvac",
        icon="mdi:fridge",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="refrigerator_door_state",
        name="Refrigerator door",
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
        name="Last updated",
        source="realtime",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BydSensorDescription(
        key="gps_last_updated",
        name="GPS last updated",
        source="realtime",
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

    entities: list[SensorEntity] = []
    for vin, coordinator in coordinators.items():
        vehicle = coordinator.data.get("vehicles", {}).get(vin)
        if vehicle is None:
            continue
        for description in SENSOR_DESCRIPTIONS:
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


class BydSensor(CoordinatorEntity, SensorEntity):
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
        self._attr_name = description.name
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

    def _get_source_obj(self) -> Any | None:
        """Return the model object for this sensor's source."""
        source_map = self.coordinator.data.get(self.entity_description.source, {})
        return source_map.get(self._vin)

    def _resolve_value(self) -> Any:
        """Extract the current value using the description's extraction logic."""
        if self.entity_description.key == "last_updated":
            return self.coordinator.get_telemetry_freshness()
        if self.entity_description.key == "gps_last_updated":
            return self.coordinator.get_gps_freshness()
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
        if self.entity_description.key == "last_updated":
            return super().available and self.coordinator.get_telemetry_freshness() is not None
        if self.entity_description.key == "gps_last_updated":
            return super().available and self.coordinator.get_gps_freshness() is not None
        return super().available and self._get_source_obj() is not None

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit; tire pressures resolve dynamically."""
        desc_unit = self.entity_description.native_unit_of_measurement
        if self.entity_description.key not in _TIRE_PRESSURE_KEYS:
            return desc_unit
        obj = self._get_source_obj()
        if obj is not None:
            api_unit = getattr(obj, "tire_press_unit", None)
            if api_unit is not None:
                return _TIRE_UNIT_MAP.get(api_unit, desc_unit)
        return desc_unit

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self._resolve_value()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this sensor."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._vin)},
            name=get_vehicle_display(self._vehicle),
            manufacturer=getattr(self._vehicle, "brand_name", None) or "BYD",
            model=getattr(self._vehicle, "model_name", None),
            serial_number=self._vin,
            hw_version=getattr(self._vehicle, "tbox_version", None) or None,
        )
