"""Tests for setup and polling."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
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
    read_meter.side_effect = NoResponseError("no valid telegram received")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


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


def test_port_aliases_share_state(tmp_path: Path) -> None:
    device = tmp_path / "ttyUSB0"
    device.touch()
    alias = tmp_path / "usb-head-if00-port0"
    alias.symlink_to(device)
    assert _canonical_port(str(alias)) == _canonical_port(str(device))
    assert _canonical_port("socket://10.0.0.2:8888") == "socket://10.0.0.2:8888"
