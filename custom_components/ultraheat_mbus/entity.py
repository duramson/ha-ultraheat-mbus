"""Base entity for the Ultraheat M-Bus integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import UltraheatCoordinator


class UltraheatEntity(CoordinatorEntity[UltraheatCoordinator]):
    """An entity of a heat meter."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: UltraheatCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        identification = str(coordinator.config_entry.unique_id)
        self._attr_unique_id = f"{identification}_{key}"
        reading = coordinator.data
        if reading is None:
            # Started without an answer from the meter: link to the known device.
            self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, identification)})
            return
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, reading.identification)},
            manufacturer=reading.manufacturer_name,
            model="Heat meter (M-Bus)",
            model_id=reading.manufacturer,
            hw_version=f"M-Bus version {reading.version}",
            sw_version=reading.firmware_version,
            serial_number=reading.fabrication_number or reading.identification,
            name=f"Heat meter {reading.identification}",
        )
