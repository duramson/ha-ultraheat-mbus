"""Config flow for the Ultraheat M-Bus integration."""

from __future__ import annotations

import logging
from typing import Any

import serialx
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_DEVICE
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SerialPortSelector,
)

from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import async_read_meter
from .mbus import InvalidFrameError, MbusError, NoResponseError

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema({vol.Required(CONF_DEVICE): SerialPortSelector()})


class UltraheatMbusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for an Ultraheat heat meter."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the serial port of the optical head and read the meter once."""
        errors: dict[str, str] = {}

        if user_input is not None:
            port = user_input[CONF_DEVICE]
            try:
                reading = await async_read_meter(self.hass, port)
            except NoResponseError:
                errors["base"] = "no_response"
            except InvalidFrameError as err:
                _LOGGER.warning("Unexpected answer from the heat meter on %s: %s", port, err)
                errors["base"] = "invalid_response"
            except (MbusError, OSError, TimeoutError, serialx.SerialException) as err:
                _LOGGER.warning("Cannot read heat meter on %s: %s", port, err)
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(reading.identification)
                self._abort_if_unique_id_configured(updates={CONF_DEVICE: port})
                return self.async_create_entry(
                    title=f"{reading.manufacturer_name} {reading.identification}",
                    data={CONF_DEVICE: port},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return UltraheatMbusOptionsFlow()


class UltraheatMbusOptionsFlow(OptionsFlowWithReload):
    """Options for the polling interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        unit_of_measurement="min",
                        mode=NumberSelectorMode.BOX,
                    )
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
