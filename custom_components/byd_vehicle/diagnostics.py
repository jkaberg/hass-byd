"""Diagnostics for BYD Vehicle integration."""

from __future__ import annotations

import dataclasses
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

REDACT_KEYS = frozenset(
    {
        "username",
        "password",
        "control_pin",
        "vin",
        "device_profile",
        "access_token",
        "refresh_token",
        "latitude",
        "longitude",
        "lat",
        "lon",
    }
)


def _redact(data: Any, depth: int = 0) -> Any:
    """Recursively redact sensitive keys from nested structures."""
    if depth > 10:
        return "..."
    if isinstance(data, dict):
        return {
            k: "**REDACTED**" if k in REDACT_KEYS else _redact(v, depth + 1)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_redact(item, depth + 1) for item in data]
    if dataclasses.is_dataclass(data) and not isinstance(data, type):
        return _redact(dataclasses.asdict(data), depth + 1)
    if hasattr(data, "__dict__"):
        return _redact(
            {k: v for k, v in data.__dict__.items() if not k.startswith("_")},
            depth + 1,
        )
    return data


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinators = entry_data.get("coordinators", {})

    coordinator_data = {}
    for vin, coordinator in coordinators.items():
        coordinator_data[vin] = _redact(coordinator.data)

    return {
        "entry": {
            "data": _redact(dict(entry.data)),
            "options": _redact(dict(entry.options)),
        },
        "coordinators": coordinator_data,
    }
