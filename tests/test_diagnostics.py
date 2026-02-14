"""Tests for diagnostics module."""

import dataclasses

from custom_components.byd_vehicle.diagnostics import REDACT_KEYS, _redact


def test_redact_flat_dict():
    data = {"username": "secret", "model": "Seal"}
    result = _redact(data)
    assert result["username"] == "**REDACTED**"
    assert result["model"] == "Seal"


def test_redact_nested_dict():
    data = {"entry": {"password": "secret", "region": "EU"}}
    result = _redact(data)
    assert result["entry"]["password"] == "**REDACTED**"
    assert result["entry"]["region"] == "EU"


def test_redact_list():
    data = [{"vin": "ABC123"}, {"vin": "DEF456"}]
    result = _redact(data)
    assert result[0]["vin"] == "**REDACTED**"
    assert result[1]["vin"] == "**REDACTED**"


def test_redact_dataclass():
    @dataclasses.dataclass
    class FakeConfig:
        username: str = "me"
        region: str = "EU"

    result = _redact(FakeConfig())
    assert result["username"] == "**REDACTED**"
    assert result["region"] == "EU"


def test_redact_depth_limit():
    # Build deeply nested dict
    data = {"a": "b"}
    for _ in range(15):
        data = {"nested": data}
    result = _redact(data)
    # Should truncate at depth 10 — the dict at depth 10 has its value replaced
    current = result
    for _ in range(10):
        current = current["nested"]
    assert current["nested"] == "..."


def test_redact_keys_contains_expected():
    assert "username" in REDACT_KEYS
    assert "password" in REDACT_KEYS
    assert "vin" in REDACT_KEYS
    assert "latitude" in REDACT_KEYS
    assert "longitude" in REDACT_KEYS
    assert "access_token" in REDACT_KEYS
