"""Tests for the config and options flow."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ultraheat_mbus.const import DOMAIN
from custom_components.ultraheat_mbus.mbus import InvalidFrameError, NoResponseError
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import PORT


async def test_user_flow(hass: HomeAssistant, read_meter: MagicMock) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device": PORT}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Landis+Gyr 12345678"
    assert result["data"] == {"device": PORT}
    assert result["result"].unique_id == "12345678"


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (NoResponseError("no valid telegram received"), "no_response"),
        (InvalidFrameError("no telegram with current values"), "invalid_response"),
        (OSError("device busy"), "cannot_connect"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, read_meter: MagicMock, error: Exception, key: str
) -> None:
    read_meter.side_effect = error
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device": PORT}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}

    read_meter.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device": PORT}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_already_configured_updates_port(
    hass: HomeAssistant, config_entry: MockConfigEntry, read_meter: MagicMock
) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device": "/dev/ttyUSB1"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert config_entry.data["device"] == "/dev/ttyUSB1"


async def test_options_flow(
    hass: HomeAssistant, config_entry: MockConfigEntry, read_meter: MagicMock
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 30}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {"scan_interval": 30}
    assert config_entry.runtime_data.update_interval.total_seconds() == 30 * 60
