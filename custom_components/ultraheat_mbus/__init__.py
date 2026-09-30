"""Ultraheat heat meters read over the optical M-Bus interface."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import UltraheatConfigEntry, UltraheatCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: UltraheatConfigEntry) -> bool:
    """Set up a heat meter from a config entry."""
    coordinator = UltraheatCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: UltraheatConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: UltraheatConfigEntry) -> None:
    """Reload after the options have changed."""
    await hass.config_entries.async_reload(entry.entry_id)
