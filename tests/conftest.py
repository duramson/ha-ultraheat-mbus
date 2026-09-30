"""Fixtures for the Home Assistant tests."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ultraheat_mbus.const import DOMAIN
from custom_components.ultraheat_mbus.mbus import MeterReading, parse_readout

FIXTURE = Path(__file__).parent / "fixtures" / "t230_readout.hex"
PORT = "/dev/serial/by-id/usb-test-if00-port0"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable the custom integration in all tests."""


@pytest.fixture(autouse=True)
def no_read_interval() -> Generator[None]:
    """Do not wait a minute between two readouts."""
    with patch("custom_components.ultraheat_mbus.coordinator.MIN_READ_INTERVAL", 0):
        yield


@pytest.fixture(name="reading")
def reading_fixture() -> MeterReading:
    """A reading of the recorded T230 readout (anonymised)."""
    return parse_readout(bytes.fromhex(FIXTURE.read_text().strip()))


@pytest.fixture(name="read_meter")
def read_meter_fixture(reading: MeterReading) -> Generator[MagicMock]:
    """Replace the serial readout."""
    with patch(
        "custom_components.ultraheat_mbus.coordinator.read_meter", return_value=reading
    ) as mock:
        yield mock


@pytest.fixture(name="config_entry")
def config_entry_fixture() -> MockConfigEntry:
    """A config entry for the meter of the fixture."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Landis+Gyr 12345678",
        unique_id="12345678",
        data={"device": PORT},
    )
