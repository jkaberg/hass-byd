"""Base entity mixins for BYD Vehicle."""

from __future__ import annotations

import logging
from collections.abc import Callable
from time import monotonic
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

#: Maximum seconds to hold optimistic state before falling back to API data.
_OPTIMISTIC_TTL_SECONDS: float = 300.0


class BydVehicleEntity(CoordinatorEntity[DataUpdateCoordinator[dict[str, Any]]]):
    """Mixin providing common properties for BYD vehicle entities.

    Subclasses must set ``_vin`` and ``_vehicle`` before calling ``super().__init__``.
    """

    _vin: str
    _vehicle: Any
    _command_pending: bool = False
    _commanded_at: float | None = None

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

    def _is_vehicle_on(self) -> bool:
        """Return True when the realtime feed reports the vehicle is on."""
        realtime = self._get_realtime()
        if realtime is None:
            return False
        return bool(getattr(realtime, "is_vehicle_on", False))

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
        self._commanded_at = monotonic()
        self.async_write_ha_state()

    def _is_command_confirmed(self) -> bool:
        """Return True when coordinator data confirms the commanded state.

        **Any entity that calls** ``_execute_command()`` **must override this
        method.**  The default ``True`` causes the optimistic flag to be
        cleared on the very next coordinator update — before the car has
        actually acted — making the UI revert to stale API data instantly.

        Override to compare the relevant coordinator fields against the
        expected state and return ``False`` while data has not yet caught
        up, so the optimistic display is held until confirmation arrives.
        """
        return True

    def _handle_coordinator_update(self) -> None:
        """Clear optimistic flag when data confirms the command or TTL expires."""
        if self._command_pending:
            ttl_expired = (
                self._commanded_at is not None
                and (monotonic() - self._commanded_at) >= _OPTIMISTIC_TTL_SECONDS
            )
            if self._is_command_confirmed() or ttl_expired:
                self._command_pending = False
                self._commanded_at = None
        super()._handle_coordinator_update()
