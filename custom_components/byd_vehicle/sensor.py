"""Sensors for BYD Vehicle."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN
from .coordinator import (
    BydDataUpdateCoordinator,
    BydGpsUpdateCoordinator,
    expand_metrics,
    extract_raw,
    get_vehicle_display,
)


_UNIT_HINTS: dict[str, str] = {
    "percent": PERCENTAGE,
    "mileage": "km",
    "endurance": "km",
    "speed": "km/h",
    "temp": "C",
    "pressure": "bar",
    "direction": "deg",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: BydDataUpdateCoordinator = data["coordinator"]
    gps_coordinator: BydGpsUpdateCoordinator = data["gps_coordinator"]

    entities: list[SensorEntity] = []

    vehicle_map = coordinator.data.get("vehicles", {})

    for vin, vehicle in vehicle_map.items():
        for category, source_key, source in (
            ("vehicle", "vehicle", vehicle_map),
            ("realtime", "realtime", coordinator.data.get("realtime", {})),
            ("energy", "energy", coordinator.data.get("energy", {})),
            ("gps", "gps", gps_coordinator.data.get("gps", {})),
        ):
            metrics = expand_metrics(source.get(vin)) if source.get(vin) is not None else {}
            for field in metrics:
                entities.append(
                    BydMetricSensor(
                        coordinator if source_key != "gps" else gps_coordinator,
                        vin,
                        vehicle,
                        category,
                        field,
                        source_key,
                    )
                )

    async_add_entities(entities)


class BydMetricSensor(CoordinatorEntity, SensorEntity):
    """Representation of a BYD vehicle metric."""

    def __init__(
        self,
        coordinator: BydDataUpdateCoordinator | BydGpsUpdateCoordinator,
        vin: str,
        vehicle: Any,
        category: str,
        field: str,
        source_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._vin = vin
        self._vehicle = vehicle
        self._category = category
        self._field = field
        self._source_key = source_key
        self._attr_unique_id = f"{vin}_{category}_{field}"
        self._attr_name = f"{get_vehicle_display(vehicle)} {category} {field}".replace("_", " ")

    @property
    def native_value(self) -> Any:
        if self._source_key == "vehicle":
            source = self.coordinator.data.get("vehicles", {})
        else:
            source = self.coordinator.data.get(self._source_key, {})
        metrics = expand_metrics(source.get(self._vin)) if source.get(self._vin) is not None else {}
        return metrics.get(self._field)

    @property
    def native_unit_of_measurement(self) -> str | None:
        for hint, unit in _UNIT_HINTS.items():
            if hint in self._field:
                return unit
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self._source_key == "vehicle":
            source = self.coordinator.data.get("vehicles", {})
        else:
            source = self.coordinator.data.get(self._source_key, {})
        raw = extract_raw(source.get(self._vin)) if source.get(self._vin) is not None else None
        attrs: dict[str, Any] = {
            "vin": self._vin,
            "category": self._category,
            "field": self._field,
        }
        if raw:
            attrs["raw"] = raw
        return attrs

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._vin)},
            name=get_vehicle_display(self._vehicle),
            manufacturer=self._vehicle.brand_name or "BYD",
            model=self._vehicle.model_name or None,
        )
