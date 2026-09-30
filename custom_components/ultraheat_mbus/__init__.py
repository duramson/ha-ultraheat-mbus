"""Ultraheat heat meters read over the optical M-Bus interface."""

from __future__ import annotations

import logging

import serialx

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_HISTORY_IMPORTED
from .coordinator import UltraheatConfigEntry, UltraheatCoordinator
from .history import async_import_history
from .mbus import MbusError

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: UltraheatConfigEntry) -> bool:
    """Set up a heat meter from a config entry."""
    coordinator = UltraheatCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if not entry.data.get(CONF_HISTORY_IMPORTED):
        entry.async_create_background_task(
            hass, _async_import_history_once(hass, entry), "ultraheat_mbus history import"
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: UltraheatConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_import_history_once(hass: HomeAssistant, entry: UltraheatConfigEntry) -> None:
    """Import the meter's storage values once after the first setup.

    Reading all telegrams keeps the meter awake for about 20 s, so this is not
    repeated on every start. It is tried again on the next start if it failed.
    """
    try:
        await async_import_history(hass, entry)
    except (HomeAssistantError, MbusError, OSError, TimeoutError, serialx.SerialException) as err:
        _LOGGER.warning("Could not import the history of heat meter %s: %s", entry.title, err)
        return
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_HISTORY_IMPORTED: True}
    )
