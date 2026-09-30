"""Tests for the M-Bus protocol module (no Home Assistant needed)."""

from datetime import datetime
from pathlib import Path
import sys

import pytest

sys.path.insert(
    0, str(Path(__file__).parents[1] / "custom_components" / "ultraheat_mbus")
)

import mbus  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "t230_readout.hex"


@pytest.fixture(name="stream")
def stream_fixture() -> bytes:
    """A real T230 readout including the echoed wake-up and request.

    Identification and fabrication number are anonymised, checksums recalculated.
    """
    return bytes.fromhex(FIXTURE.read_text().strip())


def test_extract_frames_skips_echo(stream: bytes) -> None:
    frames = mbus.extract_long_frames(stream)
    assert len(frames) == 7
    assert all(frame[0] == 0x68 and frame[-1] == 0x16 for frame in frames)


def test_bad_checksum_is_ignored(stream: bytes) -> None:
    frames = mbus.extract_long_frames(stream)
    broken = bytearray(frames[0])
    broken[-2] ^= 0xFF
    assert mbus.extract_long_frames(bytes(broken)) == []


def test_parse_readout(stream: bytes) -> None:
    reading = mbus.parse_readout(stream)

    assert reading.identification == "12345678"
    assert reading.manufacturer == "LUG"
    assert reading.manufacturer_name == "Landis+Gyr"
    assert reading.version == 7
    assert reading.medium == 0x04
    assert reading.fabrication_number == "87654321"

    assert reading.heat_energy == 143
    assert reading.volume == 13.97
    assert reading.power == 0
    assert reading.volume_flow == 0.002
    assert reading.flow_temperature == 44.0
    assert reading.return_temperature == 43.8
    assert reading.temperature_difference == 0.2
    assert reading.operating_time == 2458
    assert reading.error_time == 0
    assert reading.meter_time == datetime(2026, 9, 30, 16, 56)


def test_first_telegram_announces_more(stream: bytes) -> None:
    reading = mbus.parse_readout(stream)
    first = reading.telegrams[0]
    assert first.more_records_follow
    assert first.access_number == 0


def test_maximum_values_are_not_current(stream: bytes) -> None:
    first = mbus.parse_readout(stream).telegrams[0]
    maxima = [r for r in first.records if r.function == mbus.FUNCTION_MAXIMUM]
    assert any(r.quantity == "power" and r.value == 4000 for r in maxima)
    assert first.current("power").value == 0


def test_only_echo_raises() -> None:
    with pytest.raises(mbus.NoResponseError):
        mbus.parse_readout(mbus.PREAMBLE + mbus.REQ_UD2)


def test_strip_echo() -> None:
    assert mbus._strip_echo(mbus.PREAMBLE + mbus.REQ_UD2) == b""
    assert mbus._strip_echo(mbus.PREAMBLE + mbus.REQ_UD2 + b"\x68\x01") == b"\x68\x01"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (bytes.fromhex("08115e39"), datetime(2026, 9, 30, 17, 8)),
        (bytes.fromhex("88115e39"), None),  # invalid flag
    ],
)
def test_type_f(data: bytes, expected: datetime | None) -> None:
    assert mbus.decode_type_f(data) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (bytes.fromhex("43010000"), 143),
        (bytes.fromhex("050000f0"), -5),
    ],
)
def test_bcd(raw: bytes, expected: int) -> None:
    assert mbus._decode_bcd(raw) == expected
