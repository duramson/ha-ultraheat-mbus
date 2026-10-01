"""Ultraheat heat meters read over the optical M-Bus interface."""

from __future__ import annotations

import logging

import serialx

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import CONF_HISTORY_IMPORTED, DOMAIN
from .coordinator import UltraheatConfigEntry, UltraheatCoordinator
from .history import async_import_history
from .mbus import MbusError

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: UltraheatConfigEntry) -> bool:
    """Set up a heat meter from a config entry."""
    coordinator = UltraheatCoordinator(hass, entry)
    # A single attempt: Home Assistant retries a failed setup about every minute, and
    # each attempt wakes the battery-powered meter. If the meter does not answer, the
    # known entities start unavailable and the regular schedule tries again.
    await coordinator.async_refresh()
    if coordinator.data is None and not er.async_entries_for_config_entry(
        er.async_get(hass), entry.entry_id
    ):
        # Nothing known about the meter yet, the entities cannot be created.
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="no_answer",
            translation_placeholders={"port": coordinator.port},
        )
    entry.runtime_data = coordinator
    coordinator.async_start_polling()
    entry.async_on_unload(coordinator.async_stop_polling)

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
