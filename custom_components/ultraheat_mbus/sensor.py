"""Sensors for the Ultraheat M-Bus integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    Platform,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import UltraheatConfigEntry, UltraheatCoordinator
from .entity import UltraheatEntity
from .mbus import MeterReading


@dataclass(frozen=True, kw_only=True)
class UltraheatSensorEntityDescription(SensorEntityDescription):
    """Describes an Ultraheat sensor."""

    value_fn: Callable[[MeterReading], StateType]


SENSORS: tuple[UltraheatSensorEntityDescription, ...] = (
    UltraheatSensorEntityDescription(
        key="heat_energy",
        translation_key="heat_energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_fn=lambda r: r.heat_energy,
    ),
    UltraheatSensorEntityDescription(
        key="volume",
        translation_key="volume",
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda r: r.volume,
    ),
    UltraheatSensorEntityDescription(
        key="power",
        translation_key="power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda r: r.power,
    ),
    UltraheatSensorEntityDescription(
        key="volume_flow",
        translation_key="volume_flow",
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda r: r.volume_flow,
    ),
    UltraheatSensorEntityDescription(
        key="flow_temperature",
        translation_key="flow_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda r: r.flow_temperature,
    ),
    UltraheatSensorEntityDescription(
        key="return_temperature",
        translation_key="return_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda r: r.return_temperature,
    ),
    UltraheatSensorEntityDescription(
        # A temperature difference must not use the temperature device class, which
        # would convert it like an absolute temperature.
        key="temperature_difference",
        translation_key="temperature_difference",
        native_unit_of_measurement=UnitOfTemperature.KELVIN,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda r: r.temperature_difference,
    ),
    UltraheatSensorEntityDescription(
        key="operating_time",
        translation_key="operating_time",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
        value_fn=lambda r: r.operating_time,
    ),
    UltraheatSensorEntityDescription(
        key="error_time",
        translation_key="error_time",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
        value_fn=lambda r: r.error_time,
    ),
    UltraheatSensorEntityDescription(
        # The meter clock runs on local standard time without daylight saving and
        # carries no time zone, so it is exposed as text.
        key="meter_time",
        translation_key="meter_time",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda r: r.meter_time.isoformat(sep=" ") if r.meter_time else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UltraheatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    added: set[str] = set()

    def add(keys: set[str]) -> None:
        new = [d for d in SENSORS if d.key in keys - added]
        added.update(d.key for d in new)
        if new:
            async_add_entities(UltraheatSensor(coordinator, d) for d in new)

    @callback
    def add_reported() -> None:
        """Add a sensor for every value the meter reports."""
        if (reading := coordinator.data) is not None:
            add({d.key for d in SENSORS if d.value_fn(reading) is not None} | {"meter_time"})

    if coordinator.data is None:
        # Started without an answer from the meter: restore the sensors it had before.
        prefix = f"{entry.unique_id}_"
        add(
            {
                registry_entry.unique_id.removeprefix(prefix)
                for registry_entry in er.async_entries_for_config_entry(
                    er.async_get(hass), entry.entry_id
                )
                if registry_entry.domain == Platform.SENSOR
            }
        )
    add_reported()
    # Values the meter did not report at the start get their sensor when they appear.
    entry.async_on_unload(coordinator.async_add_listener(add_reported))


class UltraheatSensor(UltraheatEntity, SensorEntity):
    """A value of the heat meter."""

    entity_description: UltraheatSensorEntityDescription

    def __init__(
        self,
        coordinator: UltraheatCoordinator,
        description: UltraheatSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """Return the current value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
