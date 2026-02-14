"""Extension module for undocumented BYD API endpoints.

Uses pybyd's internal transport layer to call endpoints not yet exposed
by the library. This avoids forking pybyd while allowing access to
smart charging, vehicle rename, and push notification controls.

WARNING: Accesses private pybyd internals (_config, _session,
_require_transport). May break with pybyd library updates.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from pybyd import BydApiError, BydClient
from pybyd._api._envelope import build_token_outer_envelope
from pybyd._crypto import aes_decrypt_utf8
from pybyd._transport import SecureTransport
from pybyd.config import BydConfig
from pybyd.session import Session

_LOGGER = logging.getLogger(__name__)

# Undocumented endpoint paths
_EP_CHARGE_TOGGLE = "/control/smartCharge/changeChargeStatue"
_EP_CHARGE_SAVE = "/control/smartCharge/saveOrUpdate"
_EP_VEHICLE_RENAME = "/control/vehicle/modifyAutoAlias"
_EP_PUSH_GET = "/app/push/getPushSwitchState"
_EP_PUSH_SET = "/app/push/setPushSwitchState"


@dataclass(frozen=True)
class SmartChargingConfig:
    """Smart charging schedule configuration."""

    target_soc: int = 80
    start_hour: int = 0
    start_minute: int = 0
    end_hour: int = 6
    end_minute: int = 0


@dataclass(frozen=True)
class PushNotificationState:
    """Push notification toggle state."""

    enabled: bool = True


def _get_internals(
    client: BydClient,
) -> tuple[BydConfig, Session, SecureTransport]:
    """Extract internal transport objects from a BydClient instance."""
    config: BydConfig = client._config  # noqa: SLF001
    session: Session = client._session  # noqa: SLF001
    transport: SecureTransport = client._require_transport()  # noqa: SLF001
    return config, session, transport


def _build_inner(
    config: BydConfig,
    session: Session,
    vin: str,
    **extra: Any,
) -> dict[str, Any]:
    """Build the inner payload dict used by BYD API endpoints."""
    payload: dict[str, Any] = {
        "vin": vin,
        "userId": session.user_id,
        "countryCode": config.country_code,
        "language": config.language,
    }
    payload.update(extra)
    return payload


async def _call_endpoint(
    client: BydClient,
    path: str,
    vin: str,
    **extra: Any,
) -> dict[str, Any]:
    """Call an undocumented BYD API endpoint and return the parsed response."""
    config, session, transport = _get_internals(client)
    inner = _build_inner(config, session, vin, **extra)
    now_ms = int(time.time() * 1000)
    envelope, content_key = build_token_outer_envelope(
        config, session, inner, now_ms
    )

    raw = await transport.post_secure(path, envelope)

    respond_data = raw.get("respondData")
    if respond_data is None:
        code = raw.get("code", raw.get("status", "unknown"))
        msg = raw.get("msg", raw.get("message", ""))
        raise BydApiError(f"API error {code}: {msg}")

    decrypted = aes_decrypt_utf8(respond_data, content_key)
    return json.loads(decrypted)


async def toggle_smart_charging(
    client: BydClient,
    vin: str,
    *,
    enable: bool,
) -> dict[str, Any]:
    """Toggle smart charging on/off."""
    return await _call_endpoint(
        client,
        _EP_CHARGE_TOGGLE,
        vin,
        smartChargeSwitch=1 if enable else 0,
    )


async def save_charging_schedule(
    client: BydClient,
    vin: str,
    config: SmartChargingConfig,
) -> dict[str, Any]:
    """Save a smart charging schedule."""
    return await _call_endpoint(
        client,
        _EP_CHARGE_SAVE,
        vin,
        targetSoc=config.target_soc,
        startHour=config.start_hour,
        startMinute=config.start_minute,
        endHour=config.end_hour,
        endMinute=config.end_minute,
    )


async def rename_vehicle(
    client: BydClient,
    vin: str,
    *,
    name: str,
) -> dict[str, Any]:
    """Rename a vehicle."""
    return await _call_endpoint(
        client,
        _EP_VEHICLE_RENAME,
        vin,
        autoAlias=name,
    )


async def get_push_state(
    client: BydClient,
    vin: str,
) -> PushNotificationState:
    """Get push notification state."""
    result = await _call_endpoint(client, _EP_PUSH_GET, vin)
    enabled = bool(result.get("pushSwitch", 0))
    return PushNotificationState(enabled=enabled)


async def set_push_state(
    client: BydClient,
    vin: str,
    *,
    enable: bool,
) -> dict[str, Any]:
    """Set push notification state."""
    return await _call_endpoint(
        client,
        _EP_PUSH_SET,
        vin,
        pushSwitch=1 if enable else 0,
    )
