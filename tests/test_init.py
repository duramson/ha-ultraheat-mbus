"""Tests for setup and polling."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import threading
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ultraheat_mbus.coordinator import (
    _canonical_port,
    async_read_meter,
    next_poll,
)
from custom_components.ultraheat_mbus.mbus import MeterReading, NoResponseError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util


async def test_setup(
    hass: HomeAssistant, config_entry: MockConfigEntry, read_meter: MagicMock
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.heat_meter_12345678_heat_energy").state == "143"
    assert hass.states.get("sensor.heat_meter_12345678_flow_temperature").state == "44.0"

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_retries_without_answer(
    hass: HomeAssistant, config_entry: MockConfigEntry, read_meter: MagicMock
) -> None:
    """Without any known entities, setup has to wait for a first answer."""
    read_meter.side_effect = NoResponseError("no valid telegram received")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_start_without_answer_keeps_entities(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    read_meter: MagicMock,
    reading: MeterReading,
) -> None:
    """A silent meter at a restart does not hold up the setup."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(config_entry.entry_id)

    read_meter.side_effect = NoResponseError("no valid telegram received")
    read_meter.reset_mock()
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert read_meter.call_count == 1  # tried once, not again and again
    state = hass.states.get("sensor.heat_meter_12345678_heat_energy")
    assert state.state == STATE_UNAVAILABLE

    read_meter.side_effect = None
    read_meter.return_value = reading
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=16))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.heat_meter_12345678_heat_energy").state == "143"


@pytest.mark.parametrize(
    ("now", "interval", "expected"),
    [
        ("10:20:00", 60, "10:59:00"),
        ("10:58:59", 60, "10:59:00"),
        ("10:59:00", 60, "11:59:00"),
        ("10:59:30", 60, "11:59:00"),
        ("10:13:00", 15, "10:14:00"),
        ("10:14:30", 15, "10:29:00"),
        ("23:59:30", 15, "00:14:00"),
        ("05:00:00", 360, "05:59:00"),
    ],
)
def test_next_poll(now: str, interval: int, expected: str) -> None:
    """Readouts end one minute before the interval boundaries of the day."""
    day = datetime(2026, 10, 1, tzinfo=dt_util.get_time_zone("Europe/Berlin"))
    hour, minute, second = map(int, now.split(":"))
    result = next_poll(day.replace(hour=hour, minute=minute, second=second), interval)
    assert result.strftime("%H:%M:%S") == expected
    assert result > day.replace(hour=hour, minute=minute, second=second)


def test_next_poll_daylight_saving() -> None:
    """Around a clock change the next poll is never in the past."""
    tz = dt_util.get_time_zone("Europe/Berlin")
    for day in (datetime(2026, 3, 29, tzinfo=tz), datetime(2026, 10, 25, tzinfo=tz)):
        now = day
        for _ in range(48):
            following = next_poll(now, 60)
            assert following > now
            now = following


async def test_other_meter_on_port(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    read_meter: MagicMock,
    reading: MeterReading,
) -> None:
    """Values of another meter must not end up in the configured meter's entities."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    read_meter.return_value = replace(reading, identification="99999999", heat_energy=999)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=16))
    await hass.async_block_till_done()

    state = hass.states.get("sensor.heat_meter_12345678_heat_energy")
    assert state.state == STATE_UNAVAILABLE
    assert config_entry.runtime_data.last_update_success is False
    assert config_entry.runtime_data.data.heat_energy == 143


async def test_port_stays_locked_until_readout_ends(
    hass: HomeAssistant, reading: MeterReading
) -> None:
    """A timed out readout keeps the port locked until the worker has returned."""
    release = threading.Event()
    calls: list[str] = []

    def slow_read(port: str, **kwargs: object) -> MeterReading:
        calls.append(port)
        release.wait(5)
        return reading

    with (
        patch("custom_components.ultraheat_mbus.coordinator.read_meter", slow_read),
        patch(
            "custom_components.ultraheat_mbus.coordinator.READ_TIMEOUT",
            timedelta(seconds=0.05),
        ),
    ):
        with pytest.raises(TimeoutError):
            await async_read_meter(hass, "/dev/ttyUSB-test")
        second = hass.async_create_task(async_read_meter(hass, "/dev/ttyUSB-test"))
        await asyncio.sleep(0.1)
        assert len(calls) == 1  # the second readout waits for the first worker

        release.set()
        await hass.async_block_till_done()
        assert len(calls) == 2
        assert (await second).heat_energy == 143


async def test_rolling_frame_is_switched_off(
    hass: HomeAssistant, read_meter: MagicMock
) -> None:
    """After a readout with all telegrams, the next one asks for the first only."""
    await async_read_meter(hass, "/dev/ttyUSB-test")
    await async_read_meter(hass, "/dev/ttyUSB-test")
    assert read_meter.call_args_list[0].kwargs["first_only"] is False
    assert read_meter.call_args_list[1].kwargs["first_only"] is True


async def test_rolling_frame_is_switched_off_after_all_telegrams(
    hass: HomeAssistant, read_meter: MagicMock, reading: MeterReading
) -> None:
    """Switching the rolling frame off after the history readout may have failed."""
    read_meter.return_value = replace(
        reading, telegrams=reading.telegrams[:1], rolling_frame_optical=False
    )
    await async_read_meter(hass, "/dev/ttyUSB-test")
    await async_read_meter(hass, "/dev/ttyUSB-test", all_telegrams=True)
    await async_read_meter(hass, "/dev/ttyUSB-test")
    assert read_meter.call_args_list[0].kwargs["first_only"] is False
    assert read_meter.call_args_list[2].kwargs["first_only"] is True


@pytest.mark.parametrize(
    ("manufacturer", "first_only"), [("LUG", True), ("QDS", False)]
)
async def test_rolling_frame_seen_in_the_frames(
    hass: HomeAssistant,
    read_meter: MagicMock,
    reading: MeterReading,
    manufacturer: str,
    first_only: bool,
) -> None:
    """More than one telegram arrived: the rolling frame is on, whatever the flag says.

    Only a Landis+Gyr meter is switched, others do not know the command.
    """
    read_meter.return_value = replace(
        reading, manufacturer=manufacturer, rolling_frame_optical=None
    )
    await async_read_meter(hass, "/dev/ttyUSB-test")
    await async_read_meter(hass, "/dev/ttyUSB-test")
    assert read_meter.call_args_list[1].kwargs["first_only"] is first_only


def test_port_aliases_share_state(tmp_path: Path) -> None:
    device = tmp_path / "ttyUSB0"
    device.touch()
    alias = tmp_path / "usb-head-if00-port0"
    alias.symlink_to(device)
    assert _canonical_port(str(alias)) == _canonical_port(str(device))
    assert _canonical_port("socket://10.0.0.2:8888") == "socket://10.0.0.2:8888"
