"""Base entity mixins for BYD Vehicle."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from pybyd import BydRemoteControlError
from pybyd.models.gps import GpsInfo
from pybyd.models.hvac import HvacStatus

from .const import DOMAIN
from .coordinator import BydApi, get_vehicle_display

_LOGGER = logging.getLogger(__name__)


class BydVehicleEntity(CoordinatorEntity[DataUpdateCoordinator[dict[str, Any]]]):
    """Mixin providing common properties for BYD vehicle entities.

    Subclasses must set ``_vin`` and ``_vehicle`` before calling ``super().__init__``.
    """

    _vin: str
    _vehicle: Any
    _command_pending: bool = False

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info common to every BYD entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._vin)},
            name=get_vehicle_display(self._vehicle),
            manufacturer=getattr(self._vehicle, "brand_name", None) or "BYD",
            model=getattr(self._vehicle, "model_name", None),
            serial_number=self._vin,
            hw_version=getattr(self._vehicle, "tbox_version", None) or None,
        )

    @property
    def available(self) -> bool:
        """Available when coordinator has data for this vehicle."""
        if not super().available:
            return False
        return self._vin in self.coordinator.data.get("vehicles", {})

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return VIN as the default extra attribute."""
        return {"vin": self._vin}

    # ------------------------------------------------------------------
    # Shared data helpers
    # ------------------------------------------------------------------

    def _get_hvac_status(self) -> HvacStatus | None:
        """Return the HVAC status for this VIN, or None."""
        hvac = self.coordinator.data.get("hvac", {}).get(self._vin)
        return hvac if isinstance(hvac, HvacStatus) else None

    def _get_realtime(self) -> Any | None:
        """Return the realtime data for this VIN, or None."""
        return self.coordinator.data.get("realtime", {}).get(self._vin)

    def _get_gps(self) -> GpsInfo | None:
        """Return the GPS data for this VIN, or None."""
        gps = self.coordinator.data.get("gps", {}).get(self._vin)
        return gps if isinstance(gps, GpsInfo) else None

    def _get_source_obj(self, source: str) -> Any | None:
        """Return the model object for the given data source and this VIN."""
        return self.coordinator.data.get(source, {}).get(self._vin)

    # ------------------------------------------------------------------
    # Optimistic command dispatch
    # ------------------------------------------------------------------

    async def _execute_command(
        self,
        api: BydApi,
        call: Callable[[Any], Any],
        *,
        command: str,
        on_rollback: Callable[[], None] | None = None,
    ) -> None:
        """Execute a remote command with shared error handling.

        On :class:`BydRemoteControlError` the command is treated as
        optimistically successful (warning logged).  On any other failure
        *on_rollback* is called (if provided) and the exception is
        re-raised as :class:`HomeAssistantError`.

        After a successful dispatch ``_command_pending`` is set to
        ``True`` and ``async_write_ha_state`` is called.  Callers should
        set their optimistic state **before** calling this method.
        """
        try:
            await api.async_call(call, vin=self._vin, command=command)
        except BydRemoteControlError as exc:
            _LOGGER.warning(
                "%s command sent but cloud reported failure — "
                "updating state optimistically: %s",
                command,
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            if on_rollback is not None:
                on_rollback()
            raise HomeAssistantError(str(exc)) from exc
        self._command_pending = True
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        """Clear optimistic command-pending flag on fresh data."""
        self._command_pending = False
        super()._handle_coordinator_update()
