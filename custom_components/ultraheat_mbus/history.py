"""Import the meter's due-date and monthly storage values into the statistics.

The values go into the long-term statistics of the heat energy and volume sensors, so
the energy dashboard shows the months before the integration was set up. Only times
before the first existing statistic are imported and the sums are aligned to it, so
statistics recorded by Home Assistant are never changed and importing again is safe.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_metadata,
    statistics_during_period,
)
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import CONF_DEVICE, UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter, VolumeConverter

from .const import DOMAIN
from .coordinator import UltraheatConfigEntry, async_read_meter
from .mbus import StoredValue

_LOGGER = logging.getLogger(__name__)

# sensor key -> (value, unit, unit class)
_SENSORS: dict[str, tuple[Callable[[StoredValue], float | None], str, str]] = {
    "heat_energy": (
        lambda stored: stored.heat_energy,
        UnitOfEnergy.KILO_WATT_HOUR,
        EnergyConverter.UNIT_CLASS,
    ),
    "volume": (
        lambda stored: stored.volume,
        UnitOfVolume.CUBIC_METERS,
        VolumeConverter.UNIT_CLASS,
    ),
}


async def async_import_history(hass: HomeAssistant, entry: UltraheatConfigEntry) -> int:
    """Read the meter's storage and import it; return the number of imported values."""
    if "recorder" not in hass.config.components:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="recorder_not_loaded"
        )
    reading = await async_read_meter(hass, entry.data[CONF_DEVICE], all_telegrams=True)
    if reading.identification != entry.unique_id:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="meter_changed",
            translation_placeholders={
                "port": entry.data[CONF_DEVICE],
                "expected": str(entry.unique_id),
                "found": reading.identification,
            },
        )
    registry = er.async_get(hass)
    imported = 0
    for key, (value_fn, unit, unit_class) in _SENSORS.items():
        entity_id = registry.async_get_entity_id(
            SENSOR_DOMAIN, DOMAIN, f"{reading.identification}_{key}"
        )
        if entity_id is None:
            continue
        values = [
            (stored.time, value)
            for stored in reading.history
            if (value := value_fn(stored)) is not None
        ]
        rows = await _async_rows(hass, entity_id, values)
        if not rows:
            continue
        metadata = await _async_metadata(hass, entity_id, unit, unit_class)
        async_import_statistics(hass, metadata, rows)
        imported += len(rows)
    _LOGGER.info("Imported %d stored values of heat meter %s", imported, entry.title)
    return imported


def _start(time: datetime) -> datetime:
    """Return the start of the statistics hour of a meter time.

    The meter clock carries no time zone; it runs on local standard time.
    """
    local = time.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_utc(local).replace(minute=0, second=0, microsecond=0)


async def _async_rows(
    hass: HomeAssistant, statistic_id: str, values: list[tuple[datetime, float]]
) -> list[StatisticData]:
    """Return the statistics rows to import for one sensor."""
    existing = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        {statistic_id},
        "hour",
        None,
        {"state", "sum"},
    )
    points = [(_start(time), value) for time, value in values]
    if rows := existing.get(statistic_id):
        # Align the sums to the first recorded hour and keep everything from there on.
        first = rows[0]
        if first.get("state") is None or first.get("sum") is None:
            return []
        until = dt_util.utc_from_timestamp(first["start"])
        anchor_state, anchor_sum = first["state"], first["sum"]
    else:
        # Leave the current and the previous hour to the recorder.
        until = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(
            hours=1
        )
        if not points:
            return []
        anchor_state, anchor_sum = points[0][1], 0.0
    return [
        StatisticData(start=start, state=value, sum=round(anchor_sum - anchor_state + value, 6))
        for start, value in points
        if start < until
    ]


async def _async_metadata(
    hass: HomeAssistant, statistic_id: str, unit: str, unit_class: str
) -> StatisticMetaData:
    """Return the metadata of the sensor's statistics, or what the sensor will use."""
    existing = await get_instance(hass).async_add_executor_job(
        lambda: get_metadata(hass, statistic_ids={statistic_id})
    )
    if statistic_id in existing:
        return existing[statistic_id][1]
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=None,
        source="recorder",
        statistic_id=statistic_id,
        unit_class=unit_class,
        unit_of_measurement=unit,
    )
