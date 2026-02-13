from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

_FRESHNESS_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "byd_vehicle"
    / "freshness.py"
)
_SPEC = importlib.util.spec_from_file_location("byd_freshness", _FRESHNESS_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

build_telemetry_material_snapshot = _MODULE.build_telemetry_material_snapshot
snapshot_digest = _MODULE.snapshot_digest


def test_material_snapshot_uses_only_core_fields() -> None:
    realtime = SimpleNamespace(
        elec_percent=72,
        speed=0,
        timestamp=1234567890,
        serial="NOISE",
    )
    hvac = SimpleNamespace(temp_out_car=3, pm=7, random_debug="ignore")
    charging = SimpleNamespace(soc=68, full_hour=1, full_minute=15, update_time=123)
    energy = SimpleNamespace(total_energy=155.2, avg_energy_consumption=18.3, extra="ignore")

    snapshot = build_telemetry_material_snapshot(
        realtime=realtime,
        hvac=hvac,
        charging=charging,
        energy=energy,
    )

    assert snapshot == {
        "realtime": {"elec_percent": 72, "speed": 0},
        "hvac": {"temp_out_car": 3, "pm": 7},
        "charging": {"soc": 68, "full_hour": 1, "full_minute": 15},
        "energy": {"total_energy": 155.2, "avg_energy_consumption": 18.3},
    }


def test_snapshot_digest_ignores_non_material_timestamp_churn() -> None:
    base = build_telemetry_material_snapshot(
        realtime=SimpleNamespace(elec_percent=50, speed=0, timestamp=1000),
        hvac=None,
        charging=None,
        energy=None,
    )
    churned = build_telemetry_material_snapshot(
        realtime=SimpleNamespace(elec_percent=50, speed=0, timestamp=2000),
        hvac=None,
        charging=None,
        energy=None,
    )

    assert snapshot_digest(base) == snapshot_digest(churned)


def test_snapshot_digest_changes_on_material_value_change() -> None:
    before = build_telemetry_material_snapshot(
        realtime=SimpleNamespace(elec_percent=50, speed=0),
        hvac=None,
        charging=None,
        energy=None,
    )
    after = build_telemetry_material_snapshot(
        realtime=SimpleNamespace(elec_percent=51, speed=0),
        hvac=None,
        charging=None,
        energy=None,
    )

    assert snapshot_digest(before) != snapshot_digest(after)
