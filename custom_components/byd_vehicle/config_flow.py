"""Config flow for BYD Vehicle."""

from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pybyd import (
    BydApiError,
    BydAuthenticationError,
    BydClient,
    BydControlPasswordError,
    BydTransportError,
)
from pybyd.config import BydConfig

from .const import (
    BASE_URLS,
    CLIMATE_DURATION_OPTIONS,
    CONF_BASE_URL,
    CONF_CLIMATE_DURATION,
    CONF_CONTROL_PIN,
    CONF_COUNTRY_CODE,
    CONF_DISTANCE_UNIT_MODE,
    CONF_DEBUG_DUMPS,
    CONF_DEVICE_PROFILE,
    CONF_GPS_ACTIVE_INTERVAL,
    CONF_GPS_INACTIVE_INTERVAL,
    CONF_GPS_POLL_INTERVAL,
    CONF_LANGUAGE,
    CONF_POLL_INTERVAL,
    CONF_SMART_GPS_POLLING,
    COUNTRY_OPTIONS,
    DEFAULT_CLIMATE_DURATION,
    DEFAULT_COUNTRY,
    DEFAULT_DISTANCE_UNIT_MODE,
    DEFAULT_DEBUG_DUMPS,
    DEFAULT_GPS_ACTIVE_INTERVAL,
    DEFAULT_GPS_INACTIVE_INTERVAL,
    DEFAULT_GPS_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_SMART_GPS_POLLING,
    DOMAIN,
    DISTANCE_UNIT_MODE_FOLLOW_HA,
    DISTANCE_UNIT_MODE_IMPERIAL,
    DISTANCE_UNIT_MODE_METRIC,
    MAX_GPS_ACTIVE_INTERVAL,
    MAX_GPS_INACTIVE_INTERVAL,
    MAX_GPS_POLL_INTERVAL,
    MAX_POLL_INTERVAL,
    MIN_GPS_ACTIVE_INTERVAL,
    MIN_GPS_INACTIVE_INTERVAL,
    MIN_GPS_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
)
from .device_fingerprint import async_generate_device_profile

_LOGGER = logging.getLogger(__name__)


def _bounded_int(min_value: int, max_value: int) -> vol.All:
    """Build an integer validator with explicit bounds."""
    return vol.All(vol.Coerce(int), vol.Range(min=min_value, max=max_value))


_CLIMATE_DURATION_LABELS: dict[int, str] = {
    minutes: f"{minutes} min" for minutes in CLIMATE_DURATION_OPTIONS
}
_CLIMATE_DURATION_LEGACY_CODE_TO_MINUTES: dict[int, int] = {
    1: 10,
    2: 15,
    3: 20,
    4: 25,
    5: 30,
}

_DISTANCE_UNIT_LABEL_TO_MODE: dict[str, str] = {
    "Follow Home Assistant": DISTANCE_UNIT_MODE_FOLLOW_HA,
    "Metric (km, km/h)": DISTANCE_UNIT_MODE_METRIC,
    "Imperial (mi, mph)": DISTANCE_UNIT_MODE_IMPERIAL,
}
_DISTANCE_UNIT_MODE_TO_LABEL: dict[str, str] = {
    mode: label for label, mode in _DISTANCE_UNIT_LABEL_TO_MODE.items()
}


def _normalize_climate_duration_minutes(value: Any) -> int:
    """Normalize stored climate duration values to integer minutes.

    Backwards compatible with older entries that stored a raw BYD timeSpan
    *code* (1..5) or arbitrary integers.
    """
    if value is None:
        return DEFAULT_CLIMATE_DURATION
    try:
        raw = int(value)
    except (TypeError, ValueError):
        return DEFAULT_CLIMATE_DURATION

    if raw in CLIMATE_DURATION_OPTIONS:
        return raw
    if raw in _CLIMATE_DURATION_LEGACY_CODE_TO_MINUTES:
        return _CLIMATE_DURATION_LEGACY_CODE_TO_MINUTES[raw]
    return DEFAULT_CLIMATE_DURATION


def _climate_duration_default_label(value: Any) -> str:
    minutes = _normalize_climate_duration_minutes(value)
    return _CLIMATE_DURATION_LABELS.get(
        minutes,
        _CLIMATE_DURATION_LABELS[DEFAULT_CLIMATE_DURATION],
    )


def _climate_duration_label_to_minutes(label: Any) -> int:
    if isinstance(label, int):
        return _normalize_climate_duration_minutes(label)
    if not isinstance(label, str):
        return DEFAULT_CLIMATE_DURATION
    stripped = label.strip()
    if stripped in _CLIMATE_DURATION_LABELS.values():
        # "10 min" -> 10
        try:
            return int(stripped.split(" ", 1)[0])
        except (TypeError, ValueError, IndexError):
            return DEFAULT_CLIMATE_DURATION
    return _normalize_climate_duration_minutes(stripped)


def _distance_unit_default_label(value: Any) -> str:
    mode = value if value in _DISTANCE_UNIT_MODE_TO_LABEL else DEFAULT_DISTANCE_UNIT_MODE
    return _DISTANCE_UNIT_MODE_TO_LABEL.get(
        mode, _DISTANCE_UNIT_MODE_TO_LABEL[DEFAULT_DISTANCE_UNIT_MODE]
    )


def _distance_unit_label_to_mode(label: Any) -> str:
    if not isinstance(label, str):
        return DEFAULT_DISTANCE_UNIT_MODE
    return _DISTANCE_UNIT_LABEL_TO_MODE.get(label, DEFAULT_DISTANCE_UNIT_MODE)


async def _validate_input(hass: HomeAssistant, data: dict[str, Any]) -> None:
    session = async_get_clientsession(hass)
    country_name = data[CONF_COUNTRY_CODE]
    country_code, language = COUNTRY_OPTIONS[country_name]
    time_zone = hass.config.time_zone or "UTC"
    config = BydConfig(
        username=data["username"],
        password=data["password"],
        base_url=BASE_URLS[data[CONF_BASE_URL]],
        country_code=country_code,
        language=language,
        time_zone=time_zone,
        control_pin=data.get(CONF_CONTROL_PIN) or None,
    )
    async with BydClient(config, session=session) as client:
        await client.login()
        await client.get_vehicles()


class BydVehicleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Handle a config flow for BYD Vehicle."""

    VERSION = 1

    _reauth_entry: config_entries.ConfigEntry | None = None

    def _build_user_schema(self, defaults: dict[str, Any] | None = None) -> vol.Schema:
        defaults = defaults or {}
        country_label = DEFAULT_COUNTRY
        for label, (country_code, _language) in COUNTRY_OPTIONS.items():
            if country_code == defaults.get(CONF_COUNTRY_CODE):
                country_label = label
                break

        base_url_label = "Europe"
        for label, url in BASE_URLS.items():
            if url == defaults.get(CONF_BASE_URL):
                base_url_label = label
                break

        return vol.Schema(
            {
                vol.Required(CONF_BASE_URL, default=base_url_label): vol.In(
                    list(BASE_URLS)
                ),
                vol.Required("username", default=defaults.get("username", "")): str,
                vol.Required("password", default=defaults.get("password", "")): str,
                vol.Optional(
                    CONF_CONTROL_PIN,
                    default=defaults.get(CONF_CONTROL_PIN, ""),
                ): str,
                vol.Required(
                    CONF_COUNTRY_CODE,
                    default=country_label,
                ): vol.In(list(COUNTRY_OPTIONS)),
                vol.Optional(
                    CONF_POLL_INTERVAL,
                    default=defaults.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                ): _bounded_int(MIN_POLL_INTERVAL, MAX_POLL_INTERVAL),
                vol.Optional(
                    CONF_GPS_POLL_INTERVAL,
                    default=defaults.get(
                        CONF_GPS_POLL_INTERVAL, DEFAULT_GPS_POLL_INTERVAL
                    ),
                ): _bounded_int(MIN_GPS_POLL_INTERVAL, MAX_GPS_POLL_INTERVAL),
                vol.Optional(
                    CONF_SMART_GPS_POLLING,
                    default=defaults.get(
                        CONF_SMART_GPS_POLLING,
                        DEFAULT_SMART_GPS_POLLING,
                    ),
                ): bool,
                vol.Optional(
                    CONF_GPS_ACTIVE_INTERVAL,
                    default=defaults.get(
                        CONF_GPS_ACTIVE_INTERVAL,
                        DEFAULT_GPS_ACTIVE_INTERVAL,
                    ),
                ): _bounded_int(MIN_GPS_ACTIVE_INTERVAL, MAX_GPS_ACTIVE_INTERVAL),
                vol.Optional(
                    CONF_GPS_INACTIVE_INTERVAL,
                    default=defaults.get(
                        CONF_GPS_INACTIVE_INTERVAL,
                        DEFAULT_GPS_INACTIVE_INTERVAL,
                    ),
                ): _bounded_int(
                    MIN_GPS_INACTIVE_INTERVAL,
                    MAX_GPS_INACTIVE_INTERVAL,
                ),
                vol.Optional(
                    CONF_CLIMATE_DURATION,
                    default=_climate_duration_default_label(
                        defaults.get(CONF_CLIMATE_DURATION, DEFAULT_CLIMATE_DURATION)
                    ),
                ): vol.In(list(_CLIMATE_DURATION_LABELS.values())),
                vol.Optional(
                    CONF_DEBUG_DUMPS,
                    default=defaults.get(
                        CONF_DEBUG_DUMPS,
                        DEFAULT_DEBUG_DUMPS,
                    ),
                ): bool,
                vol.Optional(
                    CONF_DISTANCE_UNIT_MODE,
                    default=_distance_unit_default_label(
                        defaults.get(CONF_DISTANCE_UNIT_MODE, DEFAULT_DISTANCE_UNIT_MODE)
                    ),
                ): vol.In(list(_DISTANCE_UNIT_LABEL_TO_MODE)),
            }
        )

    def _reauth_defaults(self) -> dict[str, Any]:
        if self._reauth_entry is None:
            return {}

        options = self._reauth_entry.options
        return {
            "username": self._reauth_entry.data.get("username", ""),
            "password": self._reauth_entry.data.get("password", ""),
            CONF_BASE_URL: self._reauth_entry.data.get(
                CONF_BASE_URL, BASE_URLS["Europe"]
            ),
            CONF_COUNTRY_CODE: self._reauth_entry.data.get(
                CONF_COUNTRY_CODE,
                COUNTRY_OPTIONS[DEFAULT_COUNTRY][0],
            ),
            CONF_CONTROL_PIN: self._reauth_entry.data.get(CONF_CONTROL_PIN, ""),
            CONF_POLL_INTERVAL: options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
            CONF_GPS_POLL_INTERVAL: options.get(
                CONF_GPS_POLL_INTERVAL,
                DEFAULT_GPS_POLL_INTERVAL,
            ),
            CONF_SMART_GPS_POLLING: options.get(
                CONF_SMART_GPS_POLLING,
                DEFAULT_SMART_GPS_POLLING,
            ),
            CONF_GPS_ACTIVE_INTERVAL: options.get(
                CONF_GPS_ACTIVE_INTERVAL,
                DEFAULT_GPS_ACTIVE_INTERVAL,
            ),
            CONF_GPS_INACTIVE_INTERVAL: options.get(
                CONF_GPS_INACTIVE_INTERVAL,
                DEFAULT_GPS_INACTIVE_INTERVAL,
            ),
            CONF_CLIMATE_DURATION: options.get(
                CONF_CLIMATE_DURATION,
                DEFAULT_CLIMATE_DURATION,
            ),
            CONF_DEBUG_DUMPS: options.get(
                CONF_DEBUG_DUMPS,
                DEFAULT_DEBUG_DUMPS,
            ),
            CONF_DISTANCE_UNIT_MODE: options.get(
                CONF_DISTANCE_UNIT_MODE,
                DEFAULT_DISTANCE_UNIT_MODE,
            ),
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the user step of the config flow."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await _validate_input(self.hass, user_input)
            except BydAuthenticationError:
                errors["base"] = "invalid_auth"
            except BydControlPasswordError:
                errors["base"] = "invalid_control_pin"
            except json.JSONDecodeError:
                _LOGGER.warning(
                    "JSONDecodeError during validation – likely an invalid control PIN"
                )
                errors["base"] = "invalid_control_pin"
            except (BydApiError, BydTransportError) as exc:
                _LOGGER.warning("BYD API error during validation: %s", exc)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during validation")
                errors["base"] = "unknown"
            else:
                base_url = BASE_URLS[user_input[CONF_BASE_URL]]
                country_name = user_input[CONF_COUNTRY_CODE]
                country_code, language = COUNTRY_OPTIONS[country_name]
                await self.async_set_unique_id(f"{user_input['username']}@{base_url}")
                if self._reauth_entry is None:
                    self._abort_if_unique_id_configured()
                else:
                    self._abort_if_unique_id_mismatch(reason="wrong_account")

                    existing_device_profile = self._reauth_entry.data.get(
                        CONF_DEVICE_PROFILE
                    )
                    if existing_device_profile is None:
                        existing_device_profile = await async_generate_device_profile(
                            self.hass
                        )
                    updated_data = {
                        **self._reauth_entry.data,
                        "username": user_input["username"],
                        "password": user_input["password"],
                        CONF_BASE_URL: base_url,
                        CONF_COUNTRY_CODE: country_code,
                        CONF_LANGUAGE: language,
                        CONF_CONTROL_PIN: user_input.get(CONF_CONTROL_PIN, ""),
                        CONF_DEVICE_PROFILE: existing_device_profile,
                    }
                    updated_options = {
                        **self._reauth_entry.options,
                        CONF_POLL_INTERVAL: user_input[CONF_POLL_INTERVAL],
                        CONF_GPS_POLL_INTERVAL: user_input[CONF_GPS_POLL_INTERVAL],
                        CONF_SMART_GPS_POLLING: user_input[CONF_SMART_GPS_POLLING],
                        CONF_GPS_ACTIVE_INTERVAL: user_input[CONF_GPS_ACTIVE_INTERVAL],
                        CONF_GPS_INACTIVE_INTERVAL: user_input[
                            CONF_GPS_INACTIVE_INTERVAL
                        ],
                        CONF_CLIMATE_DURATION: _climate_duration_label_to_minutes(
                            user_input[CONF_CLIMATE_DURATION]
                        ),
                        CONF_DEBUG_DUMPS: user_input[CONF_DEBUG_DUMPS],
                        CONF_DISTANCE_UNIT_MODE: _distance_unit_label_to_mode(
                            user_input[CONF_DISTANCE_UNIT_MODE]
                        ),
                    }

                    self.hass.config_entries.async_update_entry(
                        self._reauth_entry,
                        data=updated_data,
                        options=updated_options,
                    )
                    await self.hass.config_entries.async_reload(
                        self._reauth_entry.entry_id
                    )
                    return self.async_abort(reason="reauth_successful")

                return self.async_create_entry(
                    title=user_input["username"],
                    data={
                        "username": user_input["username"],
                        "password": user_input["password"],
                        CONF_BASE_URL: base_url,
                        CONF_COUNTRY_CODE: country_code,
                        CONF_LANGUAGE: language,
                        CONF_DEVICE_PROFILE: await async_generate_device_profile(
                            self.hass
                        ),
                        CONF_CONTROL_PIN: user_input.get(CONF_CONTROL_PIN, ""),
                    },
                    options={
                        CONF_POLL_INTERVAL: user_input[CONF_POLL_INTERVAL],
                        CONF_GPS_POLL_INTERVAL: user_input[CONF_GPS_POLL_INTERVAL],
                        CONF_SMART_GPS_POLLING: user_input[CONF_SMART_GPS_POLLING],
                        CONF_GPS_ACTIVE_INTERVAL: user_input[CONF_GPS_ACTIVE_INTERVAL],
                        CONF_GPS_INACTIVE_INTERVAL: user_input[
                            CONF_GPS_INACTIVE_INTERVAL
                        ],
                        CONF_CLIMATE_DURATION: _climate_duration_label_to_minutes(
                            user_input[CONF_CLIMATE_DURATION]
                        ),
                        CONF_DEBUG_DUMPS: user_input[CONF_DEBUG_DUMPS],
                        CONF_DISTANCE_UNIT_MODE: _distance_unit_label_to_mode(
                            user_input[CONF_DISTANCE_UNIT_MODE]
                        ),
                    },
                )

        data_schema = self._build_user_schema(self._reauth_defaults())

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def async_step_reauth(
        self, _: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle re-authentication flow."""
        self._reauth_entry = self._get_reauth_entry()
        return await self.async_step_user()

    @staticmethod
    @config_entries.callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow handler."""
        return BydVehicleOptionsFlow(config_entry)


class BydVehicleOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for BYD Vehicle."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial options step."""
        if user_input is not None:
            # Store minutes (int) rather than the human label.
            if CONF_CLIMATE_DURATION in user_input:
                user_input = {
                    **user_input,
                    CONF_CLIMATE_DURATION: _climate_duration_label_to_minutes(
                        user_input[CONF_CLIMATE_DURATION]
                    ),
                }
            if CONF_DISTANCE_UNIT_MODE in user_input:
                user_input = {
                    **user_input,
                    CONF_DISTANCE_UNIT_MODE: _distance_unit_label_to_mode(
                        user_input[CONF_DISTANCE_UNIT_MODE]
                    ),
                }
            return self.async_create_entry(title="", data=user_input)

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_POLL_INTERVAL,
                    default=self._config_entry.options.get(
                        CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
                    ),
                ): _bounded_int(MIN_POLL_INTERVAL, MAX_POLL_INTERVAL),
                vol.Optional(
                    CONF_GPS_POLL_INTERVAL,
                    default=self._config_entry.options.get(
                        CONF_GPS_POLL_INTERVAL, DEFAULT_GPS_POLL_INTERVAL
                    ),
                ): _bounded_int(MIN_GPS_POLL_INTERVAL, MAX_GPS_POLL_INTERVAL),
                vol.Optional(
                    CONF_SMART_GPS_POLLING,
                    default=self._config_entry.options.get(
                        CONF_SMART_GPS_POLLING, DEFAULT_SMART_GPS_POLLING
                    ),
                ): bool,
                vol.Optional(
                    CONF_GPS_ACTIVE_INTERVAL,
                    default=self._config_entry.options.get(
                        CONF_GPS_ACTIVE_INTERVAL, DEFAULT_GPS_ACTIVE_INTERVAL
                    ),
                ): _bounded_int(MIN_GPS_ACTIVE_INTERVAL, MAX_GPS_ACTIVE_INTERVAL),
                vol.Optional(
                    CONF_GPS_INACTIVE_INTERVAL,
                    default=self._config_entry.options.get(
                        CONF_GPS_INACTIVE_INTERVAL, DEFAULT_GPS_INACTIVE_INTERVAL
                    ),
                ): _bounded_int(
                    MIN_GPS_INACTIVE_INTERVAL,
                    MAX_GPS_INACTIVE_INTERVAL,
                ),
                vol.Optional(
                    CONF_CLIMATE_DURATION,
                    default=_climate_duration_default_label(
                        self._config_entry.options.get(
                            CONF_CLIMATE_DURATION, DEFAULT_CLIMATE_DURATION
                        )
                    ),
                ): vol.In(list(_CLIMATE_DURATION_LABELS.values())),
                vol.Optional(
                    CONF_DEBUG_DUMPS,
                    default=self._config_entry.options.get(
                        CONF_DEBUG_DUMPS,
                        DEFAULT_DEBUG_DUMPS,
                    ),
                ): bool,
                vol.Optional(
                    CONF_DISTANCE_UNIT_MODE,
                    default=_distance_unit_default_label(
                        self._config_entry.options.get(
                            CONF_DISTANCE_UNIT_MODE,
                            DEFAULT_DISTANCE_UNIT_MODE,
                        )
                    ),
                ): vol.In(list(_DISTANCE_UNIT_LABEL_TO_MODE)),
            }
        )

        return self.async_show_form(step_id="init", data_schema=data_schema)
