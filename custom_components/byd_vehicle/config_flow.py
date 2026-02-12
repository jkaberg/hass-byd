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
    CONF_BASE_URL,
    CONF_CONTROL_PIN,
    CONF_COUNTRY_CODE,
    CONF_DEVICE_PROFILE,
    CONF_GPS_ACTIVE_INTERVAL,
    CONF_GPS_INACTIVE_INTERVAL,
    CONF_GPS_POLL_INTERVAL,
    CONF_LANGUAGE,
    CONF_POLL_INTERVAL,
    CONF_SMART_GPS_POLLING,
    COUNTRY_OPTIONS,
    DEFAULT_COUNTRY,
    DEFAULT_GPS_ACTIVE_INTERVAL,
    DEFAULT_GPS_INACTIVE_INTERVAL,
    DEFAULT_GPS_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_SMART_GPS_POLLING,
    DOMAIN,
)
from .device_fingerprint import generate_device_profile

_LOGGER = logging.getLogger(__name__)


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


class BydVehicleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
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
                ): int,
                vol.Optional(
                    CONF_GPS_POLL_INTERVAL,
                    default=defaults.get(
                        CONF_GPS_POLL_INTERVAL, DEFAULT_GPS_POLL_INTERVAL
                    ),
                ): int,
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
                ): int,
                vol.Optional(
                    CONF_GPS_INACTIVE_INTERVAL,
                    default=defaults.get(
                        CONF_GPS_INACTIVE_INTERVAL,
                        DEFAULT_GPS_INACTIVE_INTERVAL,
                    ),
                ): int,
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
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
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
                        CONF_DEVICE_PROFILE,
                        generate_device_profile(),
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
                        CONF_DEVICE_PROFILE: generate_device_profile(),
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
                    },
                )

        data_schema = self._build_user_schema(self._reauth_defaults())

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def async_step_reauth(self, _: dict[str, Any]) -> dict[str, Any]:
        self._reauth_entry = self._get_reauth_entry()
        return await self.async_step_user()

    @staticmethod
    @config_entries.callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return BydVehicleOptionsFlow(config_entry)


class BydVehicleOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for BYD Vehicle."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_POLL_INTERVAL,
                    default=self._config_entry.options.get(
                        CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
                    ),
                ): int,
                vol.Optional(
                    CONF_GPS_POLL_INTERVAL,
                    default=self._config_entry.options.get(
                        CONF_GPS_POLL_INTERVAL, DEFAULT_GPS_POLL_INTERVAL
                    ),
                ): int,
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
                ): int,
                vol.Optional(
                    CONF_GPS_INACTIVE_INTERVAL,
                    default=self._config_entry.options.get(
                        CONF_GPS_INACTIVE_INTERVAL, DEFAULT_GPS_INACTIVE_INTERVAL
                    ),
                ): int,
            }
        )

        return self.async_show_form(step_id="init", data_schema=data_schema)
