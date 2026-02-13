"""Telemetry freshness helpers.

This module defines how the integration decides whether telemetry changed in a
material way between polling cycles.

Approach:
- Build a reduced snapshot from entity-relevant fields only.
- Exclude transport/meta churn such as timestamps and request metadata.
- Compare snapshots using a stable digest.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any

_TELEMETRY_REALTIME_FIELDS: tuple[str, ...] = (
    "elec_percent",
    "endurance_mileage",
    "total_mileage",
    "speed",
    "temp_in_car",
    "left_front_tire_pressure",
    "right_front_tire_pressure",
    "left_rear_tire_pressure",
    "right_rear_tire_pressure",
)

# Primary HVAC values exposed by default sensors.
_TELEMETRY_HVAC_FIELDS: tuple[str, ...] = (
    "temp_out_car",
    "pm",
)

# Core charging values backing default charging sensors.
_TELEMETRY_CHARGING_FIELDS: tuple[str, ...] = (
    "soc",
    "full_hour",
    "full_minute",
)

# Core energy values backing default energy sensors.
_TELEMETRY_ENERGY_FIELDS: tuple[str, ...] = (
    "total_energy",
    "avg_energy_consumption",
)


def _json_safe_value(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _json_safe_value(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe_value(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, (str, int, float, bool)):
        return enum_value
    return str(value)


def _extract_material_fields(obj: Any | None, fields: tuple[str, ...]) -> dict[str, Any]:
    """Extract normalized material fields from a source object.

    Missing attributes and None values are skipped so partial endpoint success
    still yields a comparable snapshot.
    """
    if obj is None:
        return {}
    result: dict[str, Any] = {}
    for attr in fields:
        if not hasattr(obj, attr):
            continue
        value = getattr(obj, attr, None)
        if value is None:
            continue
        result[attr] = _json_safe_value(value)
    return result


def build_telemetry_material_snapshot(
    *,
    realtime: Any | None,
    hvac: Any | None,
    charging: Any | None,
    energy: Any | None,
) -> dict[str, Any]:
    """Build canonical telemetry snapshot used for freshness decisions."""
    snapshot: dict[str, Any] = {}

    realtime_fields = _extract_material_fields(realtime, _TELEMETRY_REALTIME_FIELDS)
    if realtime_fields:
        snapshot["realtime"] = realtime_fields

    hvac_fields = _extract_material_fields(hvac, _TELEMETRY_HVAC_FIELDS)
    if hvac_fields:
        snapshot["hvac"] = hvac_fields

    charging_fields = _extract_material_fields(charging, _TELEMETRY_CHARGING_FIELDS)
    if charging_fields:
        snapshot["charging"] = charging_fields

    energy_fields = _extract_material_fields(energy, _TELEMETRY_ENERGY_FIELDS)
    if energy_fields:
        snapshot["energy"] = energy_fields

    return snapshot


def snapshot_digest(snapshot: dict[str, Any]) -> str | None:
    """Return a stable digest for telemetry material snapshot comparison."""
    if not snapshot:
        return None
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
