"""Diagnostics for the Ultraheat M-Bus integration.

The raw telegrams are included so that readouts of other meter models can be analysed.
Identification and fabrication numbers are zeroed in the raw telegrams and redacted in
the decoded records. The serial port is redacted as well because its path can contain
the serial number of the reading head.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant

from .coordinator import UltraheatConfigEntry
from .mbus import InvalidFrameError, Telegram, is_identifying, redact_frame

_REDACTED = "**REDACTED**"


def _telegram(telegram: Telegram) -> dict[str, Any]:
    try:
        frame_hex = redact_frame(telegram.raw).hex(" ")
    except InvalidFrameError:
        frame_hex = None
    return {
        "manufacturer": telegram.manufacturer,
        "version": telegram.version,
        "medium": telegram.medium,
        "access_number": telegram.access_number,
        "status": telegram.status,
        "more_records_follow": telegram.more_records_follow,
        "frame_hex_redacted": frame_hex,
        "records": [
            {
                **{k: v for k, v in asdict(record).items() if k != "value"},
                "value": _REDACTED if is_identifying(record) else str(record.value),
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
        "port": _REDACTED if entry.data.get(CONF_DEVICE) else None,
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
        # Undecodable frames are not included raw: identifying records in them
        # cannot be located.
        "undecoded": [
            {"length": len(frame), "error": error} for frame, error in reading.undecoded
        ],
    }
