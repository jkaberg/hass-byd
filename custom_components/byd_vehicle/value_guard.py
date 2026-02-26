"""Value guard helpers for transient upstream anomalies."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pybyd.models.gps import GpsInfo

FieldValidator = Callable[[Any, Any], Any]

_GPS_NULL_ISLAND_THRESHOLD: float = 0.1


def keep_previous_when_zero(previous: Any, incoming: Any) -> Any:
    """Return previous value when incoming is zero, otherwise incoming."""
    if incoming == 0 and previous is not None:
        return previous
    return incoming


def guard_gps_coordinates(
    previous: GpsInfo | None,
    incoming: GpsInfo | None,
) -> GpsInfo | None:
    """Return the best available GpsInfo, preferring incoming.

    Falls back to *previous* when *incoming* has None coordinates
    or suspiciously near-zero (0, 0) "Null Island" coordinates.
    On first startup (previous=None) always returns incoming.
    """
    if incoming is None:
        return previous
    if previous is None:
        return incoming
    lat, lon = incoming.latitude, incoming.longitude
    if lat is None and lon is None:
        return previous
    if (
        lat is not None
        and lon is not None
        and abs(lat) < _GPS_NULL_ISLAND_THRESHOLD
        and abs(lon) < _GPS_NULL_ISLAND_THRESHOLD
    ):
        return previous
    return incoming
