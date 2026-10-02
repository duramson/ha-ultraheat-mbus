"""Tests for the diagnostics download."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ultraheat_mbus.diagnostics import async_get_config_entry_diagnostics
from custom_components.ultraheat_mbus.mbus import extract_long_frames, parse_readout
from homeassistant.core import HomeAssistant

IDENTIFYING = ("12345678", "87654321")


async def test_diagnostics_contain_no_identification(
    hass: HomeAssistant, config_entry: MockConfigEntry, read_meter: MagicMock
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    serialized = json.dumps(diagnostics)

    for number in IDENTIFYING:
        assert number not in serialized
        # BCD as transmitted, in the hex notation of the export
        assert bytes.fromhex(number)[::-1].hex(" ") not in serialized
    assert "by-id" not in serialized
    assert diagnostics["reading"]["heat_energy"] == 143
    assert diagnostics["communication"]["last_update_success"] is True
    assert diagnostics["communication"]["counts"]["answered"] == 1

    # The exported telegrams can be analysed again with the same parser.
    stream = b"".join(bytes.fromhex(t["frame_hex_redacted"]) for t in diagnostics["telegrams"])
    assert len(extract_long_frames(stream)) == 7
    assert parse_readout(stream).heat_energy == 143
