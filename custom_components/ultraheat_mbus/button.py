"""Button to import the meter's storage values into the statistics."""

from __future__ import annotations

import serialx

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import UltraheatConfigEntry, UltraheatCoordinator
from .entity import UltraheatEntity
from .history import async_import_history
from .mbus import MbusError

IMPORT_HISTORY = ButtonEntityDescription(
    key="import_history",
    translation_key="import_history",
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UltraheatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the button."""
    async_add_entities([UltraheatImportHistoryButton(entry.runtime_data, IMPORT_HISTORY)])


class UltraheatImportHistoryButton(UltraheatEntity, ButtonEntity):
    """Read the meter's storage and import it into the statistics."""

    def __init__(
        self, coordinator: UltraheatCoordinator, description: ButtonEntityDescription
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Import the storage values."""
        try:
            await async_import_history(self.hass, self.coordinator.config_entry)
        except (MbusError, OSError, TimeoutError, serialx.SerialException) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="history_failed",
                translation_placeholders={"error": str(err)},
            ) from err
