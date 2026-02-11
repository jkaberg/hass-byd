"""Select entities for BYD Vehicle seat climate control."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pybyd.models.hvac import HvacStatus

from .const import DOMAIN
from .coordinator import BydApi, BydDataUpdateCoordinator, get_vehicle_display

SEAT_LEVEL_OPTIONS = ["Off", "Low", "Medium", "High"]
SEAT_LEVEL_TO_INT = {"Off": 0, "Low": 1, "Medium": 2, "High": 3}
INT_TO_SEAT_LEVEL = {v: k for k, v in SEAT_LEVEL_TO_INT.items()}

# Mapping from param_key → HvacStatus attribute name
_PARAM_TO_HVAC_ATTR: dict[str, str] = {
    "main_heat": "main_seat_heat_state",
    "main_ventilation": "main_seat_ventilation_state",
    "copilot_heat": "copilot_seat_heat_state",
    "copilot_ventilation": "copilot_seat_ventilation_state",
    "lr_seat_heat": "lr_seat_heat_state",
    "lr_seat_ventilation": "lr_seat_ventilation_state",
    "rr_seat_heat": "rr_seat_heat_state",
    "rr_seat_ventilation": "rr_seat_ventilation_state",
}


@dataclass(frozen=True, kw_only=True)
class BydSeatClimateDescription(SelectEntityDescription):
    """Describe a BYD seat climate select entity."""

    param_key: str
    """Keyword argument name for ``client.set_seat_climate()``."""
    hvac_attr: str
    """Attribute name on ``HvacStatus`` for current state."""


SEAT_CLIMATE_DESCRIPTIONS: tuple[BydSeatClimateDescription, ...] = (
    BydSeatClimateDescription(
        key="driver_seat_heat",
        name="Driver seat heating",
        icon="mdi:car-seat-heater",
        param_key="main_heat",
        hvac_attr="main_seat_heat_state",
    ),
    BydSeatClimateDescription(
        key="driver_seat_ventilation",
        name="Driver seat ventilation",
        icon="mdi:car-seat-cooler",
        param_key="main_ventilation",
        hvac_attr="main_seat_ventilation_state",
    ),
    BydSeatClimateDescription(
        key="passenger_seat_heat",
        name="Passenger seat heating",
        icon="mdi:car-seat-heater",
        param_key="copilot_heat",
        hvac_attr="copilot_seat_heat_state",
    ),
    BydSeatClimateDescription(
        key="passenger_seat_ventilation",
        name="Passenger seat ventilation",
        icon="mdi:car-seat-cooler",
        param_key="copilot_ventilation",
        hvac_attr="copilot_seat_ventilation_state",
    ),
    BydSeatClimateDescription(
        key="rear_left_seat_heat",
        name="Rear left seat heating",
        icon="mdi:car-seat-heater",
        param_key="lr_seat_heat",
        hvac_attr="lr_seat_heat_state",
    ),
    BydSeatClimateDescription(
        key="rear_left_seat_ventilation",
        name="Rear left seat ventilation",
        icon="mdi:car-seat-cooler",
        param_key="lr_seat_ventilation",
        hvac_attr="lr_seat_ventilation_state",
    ),
    BydSeatClimateDescription(
        key="rear_right_seat_heat",
        name="Rear right seat heating",
        icon="mdi:car-seat-heater",
        param_key="rr_seat_heat",
        hvac_attr="rr_seat_heat_state",
    ),
    BydSeatClimateDescription(
        key="rear_right_seat_ventilation",
        name="Rear right seat ventilation",
        icon="mdi:car-seat-cooler",
        param_key="rr_seat_ventilation",
        hvac_attr="rr_seat_ventilation_state",
    ),
)


def _gather_seat_climate_state(
    hvac: HvacStatus | None,
    realtime: Any | None,
) -> dict[str, int]:
    """Build current seat-climate kwargs from coordinator data.

    Returns a dict with keys matching ``set_seat_climate()`` keyword
    arguments, using HVAC data first, falling back to realtime data.
    """
    values: dict[str, int] = {}
    for param_key, hvac_attr in _PARAM_TO_HVAC_ATTR.items():
        val = None
        if hvac is not None:
            val = getattr(hvac, hvac_attr, None)
        if val is None and realtime is not None:
            val = getattr(realtime, hvac_attr, None)
        values[param_key] = int(val) if val is not None else 0

    # Steering wheel heat is part of set_seat_climate too
    sw_val = None
    if hvac is not None:
        sw_val = getattr(hvac, "steering_wheel_heat_state", None)
    if sw_val is None and realtime is not None:
        sw_val = getattr(realtime, "steering_wheel_heat_state", None)
    values["steering_wheel_heat"] = int(sw_val) if sw_val is not None else 0

    return values


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BYD seat climate select entities from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: BydDataUpdateCoordinator = data["coordinator"]
    api: BydApi = data["api"]

    entities: list[SelectEntity] = []
    vehicle_map = coordinator.data.get("vehicles", {})

    for vin, vehicle in vehicle_map.items():
        for description in SEAT_CLIMATE_DESCRIPTIONS:
            entities.append(
                BydSeatClimateSelect(coordinator, api, vin, vehicle, description)
            )

    async_add_entities(entities)


class BydSeatClimateSelect(CoordinatorEntity, SelectEntity):
    """Select entity for a single seat heating/ventilation level."""

    _attr_has_entity_name = True
    _attr_options = SEAT_LEVEL_OPTIONS

    entity_description: BydSeatClimateDescription

    def __init__(
        self,
        coordinator: BydDataUpdateCoordinator,
        api: BydApi,
        vin: str,
        vehicle: Any,
        description: BydSeatClimateDescription,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._api = api
        self._vin = vin
        self._vehicle = vehicle
        self._attr_unique_id = f"{vin}_select_{description.key}"
        self._pending_value: str | None = None

    def _get_hvac_status(self) -> HvacStatus | None:
        hvac_map = self.coordinator.data.get("hvac", {})
        hvac = hvac_map.get(self._vin)
        return hvac if isinstance(hvac, HvacStatus) else None

    def _get_realtime(self) -> Any | None:
        return self.coordinator.data.get("realtime", {}).get(self._vin)

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        return self._vin in self.coordinator.data.get("vehicles", {})

    @property
    def current_option(self) -> str | None:
        if self._pending_value is not None:
            return self._pending_value
        hvac = self._get_hvac_status()
        realtime = self._get_realtime()
        val = None
        if hvac is not None:
            val = getattr(hvac, self.entity_description.hvac_attr, None)
        if val is None and realtime is not None:
            val = getattr(realtime, self.entity_description.hvac_attr, None)
        if val is not None:
            return INT_TO_SEAT_LEVEL.get(int(val), "Off")
        return None

    async def async_select_option(self, option: str) -> None:
        """Set the seat climate level."""
        level = SEAT_LEVEL_TO_INT.get(option)
        if level is None:
            return

        self._pending_value = option

        # Gather current state for all seat climate params
        hvac = self._get_hvac_status()
        realtime = self._get_realtime()
        kwargs = _gather_seat_climate_state(hvac, realtime)

        # Override our specific parameter
        kwargs[self.entity_description.param_key] = level

        async def _call(client: Any) -> Any:
            return await client.set_seat_climate(self._vin, **kwargs)

        try:
            await self._api.async_call(
                _call,
                vin=self._vin,
                command=f"seat_climate_{self.entity_description.key}",
            )
        except Exception as exc:  # noqa: BLE001
            self._pending_value = None
            raise HomeAssistantError(str(exc)) from exc

        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"vin": self._vin}
        cmd = f"seat_climate_{self.entity_description.key}"
        last_result = self._api.get_last_remote_result(self._vin, cmd)
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
