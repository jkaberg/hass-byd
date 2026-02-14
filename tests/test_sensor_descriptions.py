"""Tests for sensor descriptions completeness."""


from custom_components.byd_vehicle.sensor import SENSOR_DESCRIPTIONS


def test_no_duplicate_keys():
    keys = [d.key for d in SENSOR_DESCRIPTIONS]
    dupes = [k for k in keys if keys.count(k) > 1]
    assert len(keys) == len(set(keys)), f"Duplicate keys: {dupes}"


def test_all_have_source():
    for desc in SENSOR_DESCRIPTIONS:
        assert desc.source in ("realtime", "charging", "energy", "hvac", "vehicle"), (
            f"{desc.key} has invalid source '{desc.source}'"
        )


def test_new_hvac_sensors_present():
    keys = {d.key for d in SENSOR_DESCRIPTIONS}
    expected = {
        "air_conditioning_mode",
        "air_run_state",
        "copilot_setting_temp_new",
        "copilot_temp",
        "electric_defrost_status",
        "front_defrost_status",
        "wind_mode",
        "wind_position",
        "wiper_heat_status",
        "cycle_choice",
    }
    missing = expected - keys
    assert not missing, f"Missing HVAC sensors: {missing}"


def test_new_vehicle_metadata_sensors_present():
    keys = {d.key for d in SENSOR_DESCRIPTIONS}
    expected = {
        "auto_plate",
        "auto_bought_time",
        "tbox_version",
        "yun_active_time",
    }
    missing = expected - keys
    assert not missing, f"Missing vehicle metadata sensors: {missing}"


def test_disabled_by_default():
    """All new sensors should be disabled by default."""
    new_keys = {
        "air_conditioning_mode", "air_run_state", "copilot_setting_temp_new",
        "copilot_temp", "electric_defrost_status", "front_defrost_status",
        "rapid_decrease_temp_state", "rapid_increase_temp_state",
        "wind_mode", "wind_position", "wiper_heat_status", "pm25_state_out_car",
        "vehicle_state", "vehicle_state_info", "auto_plate", "auto_bought_time",
        "tbox_version", "yun_active_time", "tire_press_unit", "cycle_choice",
    }
    for desc in SENSOR_DESCRIPTIONS:
        if desc.key in new_keys:
            assert desc.entity_registry_enabled_default is False, (
                f"{desc.key} should be disabled by default"
            )


def test_total_sensor_count():
    """Upstream had 64 sensors, we add 20."""
    assert len(SENSOR_DESCRIPTIONS) >= 80
