"""Select entities for BYD Vehicle seat climate control."""

# Pylint (v4+) can mis-infer dataclass-generated __init__ signatures for entity
# descriptions, causing false-positive E1123 errors.
# pylint: disable=unexpected-keyword-arg

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pybyd.models.control import SeatClimateParams
from pybyd.models.realtime import SeatHeatVentState

from .const import DOMAIN
from .coordinator import BydApi, BydDataUpdateCoordinator
from .entity import BydVehicleEntity

# Derive options from the enum – single source of truth, no duplicate mappings.
SEAT_LEVEL_OPTIONS = [s.name.lower() for s in SeatHeatVentState if s.value > 0]


def _seat_status_to_option(value: Any) -> str | None:
    """Map a seat status value to a UI option label.

    Uses ``SeatHeatVentState`` member names directly so there is no
    separate int↔string mapping to keep in sync.

    Returns ``"off"`` when *value* is ``None`` or ``NO_DATA`` (0) because
    the entity exists but no data has been received yet (vehicle may be
    off). The safe assumption is the feature exists but is idle.
    """
    if value is None:
        return "off"
    if not isinstance(value, SeatHeatVentState):
        try:
            value = SeatHeatVentState(int(value))
        except (TypeError, ValueError):
            return "off"
    if value == SeatHeatVentState.NO_DATA:
        return "off"
    return value.name.lower() if value.value > 0 else "off"


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
        icon="mdi:car-seat-heater",
        param_key="main_heat",
        hvac_attr="main_seat_heat_state",
    ),
    BydSeatClimateDescription(
        key="driver_seat_ventilation",
        icon="mdi:car-seat-cooler",
        param_key="main_ventilation",
        hvac_attr="main_seat_ventilation_state",
    ),
    BydSeatClimateDescription(
        key="passenger_seat_heat",
        icon="mdi:car-seat-heater",
        param_key="copilot_heat",
        hvac_attr="copilot_seat_heat_state",
    ),
    BydSeatClimateDescription(
        key="passenger_seat_ventilation",
        icon="mdi:car-seat-cooler",
        param_key="copilot_ventilation",
        hvac_attr="copilot_seat_ventilation_state",
    ),
    BydSeatClimateDescription(
        key="rear_left_seat_heat",
        icon="mdi:car-seat-heater",
        param_key="lr_seat_heat_state",
        hvac_attr="lr_seat_heat_state",
        entity_registry_enabled_default=False,
    ),
    BydSeatClimateDescription(
        key="rear_left_seat_ventilation",
        icon="mdi:car-seat-cooler",
        param_key="lr_seat_ventilation_state",
        hvac_attr="lr_seat_ventilation_state",
        entity_registry_enabled_default=False,
    ),
    BydSeatClimateDescription(
        key="rear_right_seat_heat",
        icon="mdi:car-seat-heater",
        param_key="rr_seat_heat_state",
        hvac_attr="rr_seat_heat_state",
        entity_registry_enabled_default=False,
    ),
    BydSeatClimateDescription(
        key="rear_right_seat_ventilation",
        icon="mdi:car-seat-cooler",
        param_key="rr_seat_ventilation_state",
        hvac_attr="rr_seat_ventilation_state",
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BYD seat climate select entities from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[str, BydDataUpdateCoordinator] = data["coordinators"]
    api: BydApi = data["api"]

    entities: list[SelectEntity] = []
    for vin, coordinator in coordinators.items():
        vehicle = coordinator.data.get("vehicles", {}).get(vin)
        if vehicle is None:
            continue
        for description in SEAT_CLIMATE_DESCRIPTIONS:
            entities.append(
                BydSeatClimateSelect(coordinator, api, vin, vehicle, description)
            )

    async_add_entities(entities)


class BydSeatClimateSelect(BydVehicleEntity, SelectEntity):
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
        self._attr_translation_key = description.key
        self._api = api
        self._vin = vin
        self._vehicle = vehicle
        self._attr_unique_id = f"{vin}_select_{description.key}"
        self._pending_value: str | None = None

    @property
    def current_option(self) -> str | None:
        """Return the currently selected option."""
        if self._pending_value is not None:
            return self._pending_value
        if self._command_pending:
            return self._pending_value
        hvac = self._get_hvac_status()
        realtime = self._get_realtime()
        val = None
        if hvac is not None:
            val = getattr(hvac, self.entity_description.hvac_attr, None)
        if val is None and realtime is not None:
            val = getattr(realtime, self.entity_description.hvac_attr, None)
        option = _seat_status_to_option(val)
        # Fallback: entity was created so the feature exists – default to 'off'.
        return option if option is not None else "off"

    async def async_select_option(self, option: str) -> None:
        """Set the seat climate level."""
        try:
            level = SeatHeatVentState[option.upper()].to_command_level()
        except KeyError:
            return

        self._pending_value = option

        # Gather current state and override our specific parameter
        hvac = self._get_hvac_status()
        realtime = self._get_realtime()
        params = SeatClimateParams.from_current_state(hvac, realtime).with_change(
            self.entity_description.param_key, level
        )

        async def _call(client: Any) -> Any:
            return await client.set_seat_climate(self._vin, params=params)

        await self._execute_command(
            self._api,
            _call,
            command=f"seat_climate_{self.entity_description.key}",
            on_rollback=lambda: setattr(self, "_pending_value", None),
        )

    def _handle_coordinator_update(self) -> None:
        """Clear optimistic state only when fresh data confirms the command."""
        if self._pending_value is not None and self._is_command_confirmed():
            self._pending_value = None
        super()._handle_coordinator_update()

    def _is_command_confirmed(self) -> bool:
        """Check whether coordinator data matches the pending selection."""
        if self._pending_value is None:
            return True
        hvac = self._get_hvac_status()
        realtime = self._get_realtime()
        val = None
        if hvac is not None:
            val = getattr(hvac, self.entity_description.hvac_attr, None)
        if val is None and realtime is not None:
            val = getattr(realtime, self.entity_description.hvac_attr, None)
        option = _seat_status_to_option(val)
        return option == self._pending_value
