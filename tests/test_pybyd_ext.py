"""Tests for pybyd extension module."""


import pytest

from custom_components.byd_vehicle.pybyd_ext import (
    PushNotificationState,
    SmartChargingConfig,
    _build_inner,
)


class FakeSession:
    user_id = "user123"
    content_key = b"0123456789abcdef"


class FakeConfig:
    country_code = "AU"
    language = "en"


def test_build_inner_basic():
    config = FakeConfig()
    session = FakeSession()
    result = _build_inner(config, session, "VIN123")
    assert result["vin"] == "VIN123"
    assert result["userId"] == "user123"
    assert result["countryCode"] == "AU"
    assert result["language"] == "en"


def test_build_inner_extra_params():
    config = FakeConfig()
    session = FakeSession()
    result = _build_inner(config, session, "VIN123", smartChargeSwitch=1)
    assert result["smartChargeSwitch"] == 1
    assert result["vin"] == "VIN123"


def test_smart_charging_config_defaults():
    cfg = SmartChargingConfig()
    assert cfg.target_soc == 80
    assert cfg.start_hour == 0
    assert cfg.end_hour == 6


def test_smart_charging_config_frozen():
    cfg = SmartChargingConfig()
    with pytest.raises(AttributeError):
        cfg.target_soc = 90


def test_push_notification_state():
    state = PushNotificationState(enabled=True)
    assert state.enabled is True
    state2 = PushNotificationState(enabled=False)
    assert state2.enabled is False


def test_push_notification_state_frozen():
    state = PushNotificationState()
    with pytest.raises(AttributeError):
        state.enabled = False
