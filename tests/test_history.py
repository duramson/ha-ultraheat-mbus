"""Tests for the import of the meter's storage values."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

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


async def test_history_imported_after_setup(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    read_meter: MagicMock,
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    energy = await _statistics(hass, ENERGY)
    assert [(row["state"], row["sum"]) for row in energy] == [
        (0, 0),
        (66, 66),
        (86, 86),
        (102, 102),
        (123, 123),
    ]
    # 31 May 2026 23:59 on the meter clock, in the test time zone
    local = datetime(2026, 5, 31, 23, 0, tzinfo=dt_util.get_default_time_zone())
    assert energy[1]["start"] == local.timestamp()
    volume = await _statistics(hass, VOLUME)
    assert [row["state"] for row in volume] == [0.0, 4.87, 7.64, 10.12, 12.27]

    assert config_entry.data["history_imported"] is True
    assert read_meter.call_args_list[-1].kwargs == {"all_telegrams": True}


async def test_import_keeps_recorded_statistics(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    read_meter: MagicMock,
) -> None:
    """Statistics recorded by Home Assistant stay as they are; history goes before."""
    entry = MockConfigEntry(
        domain="ultraheat_mbus",
        unique_id="12345678",
        data={"device": "/dev/ttyUSB0", "history_imported": True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert await _statistics(hass, ENERGY) == []

    recorded_start = datetime(2026, 9, 10, 12, tzinfo=dt_util.UTC)
    recorded = [
        {"start": recorded_start, "state": 130, "sum": 1000},
        {"start": recorded_start + timedelta(hours=1), "state": 131, "sum": 1001},
    ]
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
        recorded,
    )
    await async_wait_recording_done(hass)

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.heat_meter_12345678_import_meter_history"},
        blocking=True,
    )
    energy = await _statistics(hass, ENERGY)
    assert [(row["state"], row["sum"]) for row in energy] == [
        (0, 870),
        (66, 936),
        (86, 956),
        (102, 972),
        (123, 993),
        (130, 1000),
        (131, 1001),
    ]

    # Importing again changes nothing.
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.heat_meter_12345678_import_meter_history"},
        blocking=True,
    )
    assert len(await _statistics(hass, ENERGY)) == 7
