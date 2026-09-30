"""Diagnostics for the Ultraheat M-Bus integration.

The raw telegrams are included so that readouts of other meter models can be analysed.
Identification and fabrication numbers are redacted.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import UltraheatConfigEntry
from .mbus import Telegram

_REDACTED = "**REDACTED**"


def _redact_frame(telegram: Telegram) -> str:
    """Return the frame as hex with the identification bytes replaced."""
    frame = bytearray(telegram.raw)
    frame[7:11] = b"\x00\x00\x00\x00"
    return frame.hex(" ")


def _telegram(telegram: Telegram) -> dict[str, Any]:
    return {
        "manufacturer": telegram.manufacturer,
        "version": telegram.version,
        "medium": telegram.medium,
        "access_number": telegram.access_number,
        "status": telegram.status,
        "more_records_follow": telegram.more_records_follow,
        "frame_hex_id_redacted": _redact_frame(telegram),
        "records": [
            {
                **{k: v for k, v in asdict(record).items() if k != "value"},
                "value": _REDACTED
                if record.quantity == "fabrication_number"
                else str(record.value),
            }
            for record in telegram.records
        ],
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: UltraheatConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    reading = entry.runtime_data.data
    return {
        "port": entry.data.get("device"),
        "options": dict(entry.options),
        "reading": {
            "manufacturer": reading.manufacturer,
            "version": reading.version,
            "medium": reading.medium,
            "status": reading.status,
            "heat_energy": reading.heat_energy,
            "volume": reading.volume,
            "power": reading.power,
            "volume_flow": reading.volume_flow,
            "flow_temperature": reading.flow_temperature,
            "return_temperature": reading.return_temperature,
            "temperature_difference": reading.temperature_difference,
            "operating_time": reading.operating_time,
            "error_time": reading.error_time,
            "meter_time": str(reading.meter_time),
        },
        "telegrams": [_telegram(t) for t in reading.telegrams],
    }
