"""Tests for the import of the meter's storage values."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from homeassistant.components.recorder import Recorder, get_instance
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

ENERGY = "sensor.heat_meter_12345678_heat_energy"
VOLUME = "sensor.heat_meter_12345678_volume"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    recorder_mock: Recorder, enable_custom_integrations: None
) -> None:
    """Set up the recorder before Home Assistant, then enable the integration."""


async def _statistics(hass: HomeAssistant, statistic_id: str) -> list[dict[str, Any]]:
    await async_wait_recording_done(hass)
    result = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        {statistic_id},
        "hour",
        None,
        {"state", "sum"},
    )
    return result.get(statistic_id, [])


def _local(year: int, month: int, day: int, hour: int = 23) -> float:
    """Return the timestamp of a local time in the test time zone."""
    return datetime(year, month, day, hour, tzinfo=dt_util.get_default_time_zone()).timestamp()


def _row(rows: list[dict[str, Any]], start: float) -> tuple[float, float]:
    row = next(row for row in rows if row["start"] == start)
    return row["state"], row["sum"]


async def test_history_imported_after_setup(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    read_meter: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-09-30 17:30:00+00:00")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    energy = await _statistics(hass, ENERGY)
    # The stored values at the month ends are kept exactly ...
    assert _row(energy, _local(2025, 12, 31)) == (0, 0)
    assert _row(energy, _local(2026, 5, 31)) == (66, 66)
    assert _row(energy, _local(2026, 8, 31)) == (123, 123)
    # ... the days in between are interpolated ...
    assert _row(energy, _local(2026, 6, 15)) == (76, 76)
    # ... and the last value is the current reading, in the hour before the current one.
    assert energy[-1]["start"] == datetime(2026, 9, 30, 16, tzinfo=dt_util.UTC).timestamp()
    assert (energy[-1]["state"], energy[-1]["sum"]) == (143, 143)
    assert len(energy) == 274  # one value per day from 31 Dec, plus the last one
    assert [row["sum"] for row in energy] == sorted(row["sum"] for row in energy)

    volume = await _statistics(hass, VOLUME)
    assert _row(volume, _local(2026, 8, 31)) == (12.27, 12.27)

    until = datetime(2026, 9, 30, 17, tzinfo=dt_util.UTC).timestamp()
    assert config_entry.data["history_imported"] is True
    assert config_entry.data["history_until"] == {"heat_energy": until, "volume": until}
    assert read_meter.call_args_list[-1].kwargs == {"all_telegrams": True, "first_only": False}


async def test_import_replaces_imported_values_only(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    read_meter: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An entry from 0.3.0 gets its month blocks spread; recorded consumption stays."""
    freezer.move_to("2026-09-10 12:30:00+00:00")
    entry = MockConfigEntry(
        domain="ultraheat_mbus",
        unique_id="12345678",
        data={"device": "/dev/ttyUSB0", "history_imported": True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    recorded_start = datetime(2026, 9, 10, 12, tzinfo=dt_util.UTC)
    async_import_statistics(
        hass,
        {
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
            "name": None,
            "source": "recorder",
            "statistic_id": ENERGY,
            "unit_class": "energy",
            "unit_of_measurement": "kWh",
        },
        [
            # a month block as imported by 0.3.0
            {"start": dt_util.utc_from_timestamp(_local(2026, 8, 31)), "state": 123, "sum": 993},
            # recorded by Home Assistant
            {"start": recorded_start, "state": 130, "sum": 1000},
            {"start": recorded_start + timedelta(hours=1), "state": 131, "sum": 1001},
        ],
    )
    await async_wait_recording_done(hass)

    for _ in range(2):  # importing again gives the same result
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.heat_meter_12345678_import_meter_history"},
            blocking=True,
        )
        energy = await _statistics(hass, ENERGY)
        # The sums start at 0 with the oldest value; the recorded ones are shifted.
        assert energy[0]["sum"] == 0
        assert _row(energy, _local(2026, 8, 31)) == (123, 123)
        assert _row(energy, _local(2026, 5, 31)) == (66, 66)
        # 1 to 9 September: the 7 kWh up to the first recorded hour are spread
        assert _row(energy, _local(2026, 9, 5)) == (126.784, 126.784)
        assert [(row["start"], row["state"], row["sum"]) for row in energy[-2:]] == [
            (recorded_start.timestamp(), 130, 130),
            (recorded_start.timestamp() + 3600, 131, 131),
        ]
    assert entry.data["history_until"]["heat_energy"] == recorded_start.timestamp()
