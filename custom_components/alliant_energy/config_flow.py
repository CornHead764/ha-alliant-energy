"""Config flow for Alliant Energy integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

from .client import AlliantEnergyClient, AlliantEnergyAuthError, AlliantEnergyMeter
from .const import (
    DOMAIN,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_METERS,
    CONF_SELECTED_METERS,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def _discover_meters(
    hass: HomeAssistant, username: str, password: str
) -> list[AlliantEnergyMeter]:
    """Authenticate and return the electric meters on the account."""
    async with AlliantEnergyClient(username=username, password=password) as client:
        return await client.async_get_meters()


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Alliant Energy."""

    VERSION = 2

    def __init__(self) -> None:
        self._username: str | None = None
        self._password: str | None = None
        self._meters: list[AlliantEnergyMeter] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: collect and validate credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                meters = await _discover_meters(
                    self.hass,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
            except AlliantEnergyAuthError:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                self._abort_if_unique_id_configured()

                self._username = user_input[CONF_USERNAME]
                self._password = user_input[CONF_PASSWORD]
                self._meters = meters
                return await self.async_step_meters()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_meters(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: let the user choose which meters to import."""
        errors: dict[str, str] = {}
        meter_options = {m.meter_number: m.label for m in self._meters}

        if user_input is not None:
            selected = user_input[CONF_SELECTED_METERS]
            if not selected:
                errors["base"] = "no_meters_selected"
            else:
                return self.async_create_entry(
                    title=f"Alliant Energy ({self._username})",
                    data={
                        CONF_USERNAME: self._username,
                        CONF_PASSWORD: self._password,
                        CONF_METERS: {
                            m.meter_number: m.to_dict() for m in self._meters
                        },
                    },
                    options={CONF_SELECTED_METERS: selected},
                )

        return self.async_show_form(
            step_id="meters",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SELECTED_METERS,
                        default=list(meter_options),
                    ): cv.multi_select(meter_options),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> "OptionsFlowHandler":
        """Get the options flow for changing the selected meters."""
        return OptionsFlowHandler()


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle changing which meters are imported after setup.

    ``self.config_entry`` is provided by Home Assistant; modern HA exposes
    it as a read-only property, so it must not be set in ``__init__``.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Re-discover meters and let the user change the selection."""
        errors: dict[str, str] = {}

        try:
            meters = await _discover_meters(
                self.hass,
                self.config_entry.data[CONF_USERNAME],
                self.config_entry.data[CONF_PASSWORD],
            )
        except AlliantEnergyAuthError:
            # Fall back to the meters cached on the entry so the user can
            # still edit their selection while auth is temporarily failing.
            stored = self.config_entry.data.get(CONF_METERS, {})
            meters = [AlliantEnergyMeter.from_dict(m) for m in stored.values()]
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception discovering meters")
            stored = self.config_entry.data.get(CONF_METERS, {})
            meters = [AlliantEnergyMeter.from_dict(m) for m in stored.values()]

        meter_options = {m.meter_number: m.label for m in meters}
        current = [
            mn
            for mn in self.config_entry.options.get(CONF_SELECTED_METERS, [])
            if mn in meter_options
        ] or list(meter_options)

        if user_input is not None:
            selected = user_input[CONF_SELECTED_METERS]
            if not selected:
                errors["base"] = "no_meters_selected"
            else:
                # Refresh the cached meter metadata on the entry so labels and
                # any new/removed meters stay in sync.
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={
                        **self.config_entry.data,
                        CONF_METERS: {
                            m.meter_number: m.to_dict() for m in meters
                        },
                    },
                )
                return self.async_create_entry(
                    title="",
                    data={CONF_SELECTED_METERS: selected},
                )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SELECTED_METERS,
                        default=current,
                    ): cv.multi_select(meter_options),
                }
            ),
            errors=errors,
        )
