"""Read Landis+Gyr Ultraheat T230/T330 heat meters over the optical M-Bus interface.

This module has no Home Assistant dependencies so it can be used and tested on its own.

Protocol summary (EN 13757-2/-3 over the optical interface, EN 62056-21 hardware):

* The serial line runs at 2400 baud, 8 data bits, even parity, 1 stop bit throughout.
* The meter sleeps. It is woken by a preamble of ``0x00`` bytes. The request has to
  follow the preamble within 11-330 bit times (about 4.6-137 ms at 2400 baud), so the
  preamble and the request are written in a single call without any pause.
* ``REQ_UD2`` (``10 7B FE 79 16``) to the broadcast address makes the meter answer with
  its full data set as a series of RSP_UD long frames: current values first, followed by
  frames with due-date and monthly storage values.
* The manufacturer documents a minimum of one minute between readouts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import struct
import time
from typing import Any

BAUDRATE = 2400
PREAMBLE = b"\x00" * 240
REQ_UD2 = bytes.fromhex("107BFE7916")
MIN_READ_INTERVAL = 60  # seconds, from the T230 technical description

CI_RSP_UD_LONG_HEADER = 0x72

FUNCTION_INSTANTANEOUS = 0
FUNCTION_MAXIMUM = 1
FUNCTION_MINIMUM = 2
FUNCTION_ERROR_STATE = 3

MANUFACTURERS = {
    "LUG": "Landis+Gyr",
    "QDS": "Qundis",
    "IST": "ista",
}

MEDIUMS = {
    0x04: "heat (outlet)",
    0x0C: "heat (inlet)",
    0x0D: "heat / cooling",
    0x0A: "cooling (outlet)",
    0x0B: "cooling (inlet)",
}

# Data field (lower nibble of the DIF) -> length in bytes. 0x0D is variable length.
_DATA_LENGTH = {
    0x0: 0, 0x1: 1, 0x2: 2, 0x3: 3, 0x4: 4, 0x5: 4, 0x6: 6, 0x7: 8,
    0x8: 0, 0x9: 1, 0xA: 2, 0xB: 3, 0xC: 4, 0xE: 6, 0xF: 0,
}
_BCD_FIELDS = {0x9, 0xA, 0xB, 0xC, 0xE}


class MbusError(Exception):
    """Base error."""


class NoResponseError(MbusError):
    """The meter did not answer."""


class InvalidFrameError(MbusError):
    """A frame could not be parsed or failed the checksum."""


@dataclass(frozen=True)
class DataRecord:
    """One decoded variable data record."""

    quantity: str
    value: float | int | datetime | str | None
    unit: str | None
    function: int
    storage: int
    tariff: int
    subunit: int
    dif: int
    vif: int
    vife: tuple[int, ...] = ()

    @property
    def is_current(self) -> bool:
        """Return True for plain instantaneous values of the main register."""
        return (
            self.function == FUNCTION_INSTANTANEOUS
            and self.storage == 0
            and self.tariff == 0
            and self.subunit == 0
        )


@dataclass(frozen=True)
class Telegram:
    """A decoded RSP_UD long frame with a long header (CI 0x72)."""

    identification: str
    manufacturer: str
    version: int
    medium: int
    access_number: int
    status: int
    records: tuple[DataRecord, ...]
    more_records_follow: bool
    raw: bytes

    @property
    def manufacturer_name(self) -> str:
        return MANUFACTURERS.get(self.manufacturer, self.manufacturer)

    def current(self, quantity: str) -> DataRecord | None:
        """Return the first current (instantaneous, storage 0) record of a quantity."""
        for record in self.records:
            if record.quantity == quantity and record.is_current:
                return record
        return None


@dataclass
class MeterReading:
    """The current values of a heat meter, taken from the first telegram."""

    identification: str
    manufacturer: str
    version: int
    medium: int
    status: int
    heat_energy: float | None = None  # kWh
    volume: float | None = None  # m³
    power: float | None = None  # W
    volume_flow: float | None = None  # m³/h
    flow_temperature: float | None = None  # °C
    return_temperature: float | None = None  # °C
    temperature_difference: float | None = None  # K
    operating_time: float | None = None  # h
    error_time: float | None = None  # h
    fabrication_number: str | None = None
    meter_time: datetime | None = None
    telegrams: list[Telegram] = field(default_factory=list)
    raw: bytes = b""

    @property
    def manufacturer_name(self) -> str:
        return MANUFACTURERS.get(self.manufacturer, self.manufacturer)


# --------------------------------------------------------------------------- framing


def _checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def extract_long_frames(stream: bytes) -> list[bytes]:
    """Return all long frames (68 L L 68 ... CS 16) with a valid checksum.

    The stream may contain the echoed preamble and request, partial frames at the end
    (when reading stopped early) and noise; those parts are skipped.
    """
    frames: list[bytes] = []
    i = 0
    while i <= len(stream) - 6:
        if stream[i] != 0x68 or stream[i + 3] != 0x68 or stream[i + 1] != stream[i + 2]:
            i += 1
            continue
        length = stream[i + 1]
        end = i + 4 + length + 2
        if end > len(stream):
            break
        body = stream[i + 4 : i + 4 + length]
        if stream[end - 1] == 0x16 and _checksum(body) == stream[end - 2]:
            frames.append(stream[i:end])
            i = end
        else:
            i += 1
    return frames


# --------------------------------------------------------------------------- values


def _decode_bcd(data: bytes) -> int:
    digits = data[::-1].hex()
    negative = digits[0] == "f"
    if negative:
        digits = digits[1:]
    if not digits.isdigit():
        raise InvalidFrameError(f"invalid BCD value {data.hex()}")
    value = int(digits)
    return -value if negative else value


def _decode_int(data: bytes) -> int:
    return int.from_bytes(data, "little", signed=True)


def decode_type_f(data: bytes) -> datetime | None:
    """Decode a CP32 date/time (type F)."""
    if len(data) != 4 or data[0] & 0x80:  # invalid flag
        return None
    minute = data[0] & 0x3F
    hour = data[1] & 0x1F
    day = data[2] & 0x1F
    month = data[3] & 0x0F
    year = ((data[2] & 0xE0) >> 5) | ((data[3] & 0xF0) >> 1)
    if year > 99:
        return None
    try:
        return datetime(2000 + year, month, day, hour, minute)
    except ValueError:
        return None


def decode_type_g(data: bytes) -> datetime | None:
    """Decode a CP16 date (type G)."""
    if len(data) != 2:
        return None
    day = data[0] & 0x1F
    month = data[1] & 0x0F
    year = ((data[0] & 0xE0) >> 5) | ((data[1] & 0xF0) >> 1)
    if year > 99:
        return None
    try:
        return datetime(2000 + year, month, day)
    except ValueError:
        return None


_DURATION_UNITS = ("s", "min", "h", "d")


def _scale(raw_value: int | float, exponent: int) -> int | float:
    """Apply a decimal exponent without introducing floating point noise."""
    if exponent >= 0:
        return raw_value * 10**exponent
    return round(raw_value * 10**exponent, -exponent)


def _interpret_vif(vif: int, raw_value: Any) -> tuple[str, Any, str | None]:
    """Map a primary VIF to (quantity, scaled value, unit)."""
    code = vif & 0x7F
    n = code & 0x07
    m = code & 0x03
    if code in (0x6C, 0x6D):
        return ("date" if code == 0x6C else "datetime"), raw_value, None
    if code <= 0x07:
        return "energy", _scale(raw_value, n - 6), "kWh"
    if code <= 0x0F:
        return "energy", round(raw_value * 10**n / 3_600_000, 6), "kWh"
    if code <= 0x17:
        return "volume", _scale(raw_value, n - 6), "m³"
    if code <= 0x1F:
        return "mass", _scale(raw_value, n - 3), "kg"
    if code <= 0x23:
        return "on_time", raw_value, _DURATION_UNITS[m]
    if code <= 0x27:
        return "operating_time", raw_value, _DURATION_UNITS[m]
    if code <= 0x2F:
        return "power", _scale(raw_value, n - 3), "W"
    if code <= 0x37:
        return "power", round(raw_value * 10**n / 3600, 3), "W"
    if code <= 0x3F:
        return "volume_flow", _scale(raw_value, n - 6), "m³/h"
    if code <= 0x47:
        return "volume_flow", round(raw_value * 10 ** (n - 7) * 60, 9), "m³/h"
    if code <= 0x4F:
        return "volume_flow", round(raw_value * 10 ** (n - 9) * 3600, 9), "m³/h"
    if code <= 0x57:
        return "mass_flow", _scale(raw_value, n - 3), "kg/h"
    if code <= 0x5B:
        return "flow_temperature", _scale(raw_value, m - 3), "°C"
    if code <= 0x5F:
        return "return_temperature", _scale(raw_value, m - 3), "°C"
    if code <= 0x63:
        return "temperature_difference", _scale(raw_value, m - 3), "K"
    if code <= 0x67:
        return "external_temperature", _scale(raw_value, m - 3), "°C"
    if code <= 0x6B:
        return "pressure", _scale(raw_value, m - 3), "bar"
    if code == 0x6E:
        return "hca_units", raw_value, None
    if code <= 0x73:
        return "averaging_duration", raw_value, _DURATION_UNITS[m]
    if code <= 0x77:
        return "actuality_duration", raw_value, _DURATION_UNITS[m]
    if code == 0x78:
        return "fabrication_number", raw_value, None
    if code == 0x79:
        return "enhanced_identification", raw_value, None
    if code == 0x7A:
        return "bus_address", raw_value, None
    return f"vif_{vif:02x}", raw_value, None


def parse_records(data: bytes) -> tuple[list[DataRecord], bool]:
    """Parse the variable data records of a telegram.

    Returns the records and whether the meter announced further telegrams (DIF 0x1F).
    """
    records: list[DataRecord] = []
    pos = 0
    more = False
    while pos < len(data):
        dif = data[pos]
        pos += 1
        if dif == 0x2F:  # idle filler
            continue
        if dif in (0x0F, 0x1F):  # manufacturer specific data up to the end
            more = dif == 0x1F
            break

        data_field = dif & 0x0F
        function = (dif >> 4) & 0x03
        storage = (dif >> 6) & 0x01
        tariff = 0
        subunit = 0
        extension = dif & 0x80
        index = 0
        while extension:
            if pos >= len(data):
                raise InvalidFrameError("truncated DIFE")
            dife = data[pos]
            pos += 1
            storage |= (dife & 0x0F) << (1 + 4 * index)
            tariff |= ((dife >> 4) & 0x03) << (2 * index)
            subunit |= ((dife >> 6) & 0x01) << index
            extension = dife & 0x80
            index += 1

        if pos >= len(data):
            raise InvalidFrameError("truncated VIF")
        vif = data[pos]
        pos += 1
        if (vif & 0x7F) == 0x7C:  # plain text VIF
            text_length = data[pos]
            pos += 1 + text_length
        vife: list[int] = []
        extension = vif & 0x80
        while extension:
            if pos >= len(data):
                raise InvalidFrameError("truncated VIFE")
            vife.append(data[pos])
            extension = data[pos] & 0x80
            pos += 1

        if data_field == 0x0D:
            length = data[pos]
            pos += 1
        else:
            length = _DATA_LENGTH[data_field]
        raw = data[pos : pos + length]
        if len(raw) != length:
            raise InvalidFrameError("truncated data field")
        pos += length

        code = vif & 0x7F
        value: Any
        if data_field in _BCD_FIELDS:
            value = _decode_bcd(raw)
        elif data_field == 0x5:
            value = struct.unpack("<f", raw)[0]
        elif code == 0x6D and data_field == 0x4:
            value = decode_type_f(raw)
        elif code == 0x6C and data_field == 0x2:
            value = decode_type_g(raw)
        elif length:
            value = _decode_int(raw)
        else:
            value = None

        if vif in (0xFB, 0xFD, 0xFF) or code == 0x7C:
            # Extension tables and manufacturer specific VIFs are kept raw.
            quantity, unit = f"vif_{vif:02x}", None
            if vif == 0xFD and vife and (vife[0] & 0x7F) == 0x17:
                quantity = "error_flags"
        elif not isinstance(value, (int, float)) and code not in (0x6C, 0x6D):
            quantity, unit = f"vif_{vif:02x}", None
        else:
            quantity, value, unit = _interpret_vif(vif, value)
            if vife:
                # A VIFE changes the meaning (e.g. time stamp of a maximum). Keep such
                # records apart from the plain quantity instead of guessing.
                quantity = f"{quantity}_vife_{bytes(vife).hex()}"

        if quantity == "fabrication_number" and isinstance(value, int):
            value = f"{value:08d}"

        records.append(
            DataRecord(
                quantity=quantity,
                value=value,
                unit=unit,
                function=function,
                storage=storage,
                tariff=tariff,
                subunit=subunit,
                dif=dif,
                vif=vif,
                vife=tuple(vife),
            )
        )
    return records, more


def parse_telegram(frame: bytes) -> Telegram:
    """Parse one RSP_UD long frame with a long header."""
    if len(frame) < 21:
        raise InvalidFrameError("frame too short")
    ci = frame[6]
    if ci != CI_RSP_UD_LONG_HEADER:
        raise InvalidFrameError(f"unsupported CI field 0x{ci:02x}")
    header = frame[7:19]
    identification = header[0:4][::-1].hex()
    manufacturer_raw = header[4] | (header[5] << 8)
    manufacturer = "".join(
        chr(((manufacturer_raw >> shift) & 0x1F) + 64) for shift in (10, 5, 0)
    )
    records, more = parse_records(frame[19:-2])
    return Telegram(
        identification=identification,
        manufacturer=manufacturer,
        version=header[6],
        medium=header[7],
        access_number=header[8],
        status=header[9],
        records=tuple(records),
        more_records_follow=more,
        raw=frame,
    )


def _to_hours(record: DataRecord | None) -> float | None:
    if record is None or not isinstance(record.value, (int, float)):
        return None
    factor = {"s": 1 / 3600, "min": 1 / 60, "h": 1, "d": 24}[record.unit or "h"]
    return record.value * factor


def parse_readout(stream: bytes) -> MeterReading:
    """Build a MeterReading from everything the meter sent after one REQ_UD2."""
    telegrams: list[Telegram] = []
    for frame in extract_long_frames(stream):
        try:
            telegrams.append(parse_telegram(frame))
        except InvalidFrameError:
            continue
    if not telegrams:
        raise NoResponseError("no valid telegram received")

    first = telegrams[0]

    def value(quantity: str) -> Any:
        record = first.current(quantity)
        return None if record is None else record.value

    error_time = next(
        (
            r
            for r in first.records
            if r.quantity == "on_time"
            and r.function == FUNCTION_ERROR_STATE
            and r.storage == 0
            and r.tariff == 0
        ),
        None,
    )
    return MeterReading(
        identification=first.identification,
        manufacturer=first.manufacturer,
        version=first.version,
        medium=first.medium,
        status=first.status,
        heat_energy=value("energy"),
        volume=value("volume"),
        power=value("power"),
        volume_flow=value("volume_flow"),
        flow_temperature=value("flow_temperature"),
        return_temperature=value("return_temperature"),
        temperature_difference=value("temperature_difference"),
        operating_time=_to_hours(first.current("operating_time")),
        error_time=_to_hours(error_time),
        fabrication_number=value("fabrication_number"),
        meter_time=value("datetime"),
        telegrams=telegrams,
        raw=stream,
    )


# --------------------------------------------------------------------------- serial I/O


def read_raw(
    port: str,
    *,
    first_byte_timeout: float = 3.0,
    idle_timeout: float = 0.5,
    max_duration: float = 10.0,
    all_telegrams: bool = False,
) -> bytes:
    """Wake the meter, request its data and return the raw bytes received.

    By default reading stops as soon as the first complete telegram (current values)
    has arrived. With ``all_telegrams`` it continues until the line is idle.
    """
    import serialx  # pylint: disable=import-outside-toplevel

    with serialx.serial_for_url(
        port,
        baudrate=BAUDRATE,
        byte_size=8,
        parity=serialx.Parity.EVEN,
        stopbits=serialx.StopBits.ONE,
        read_timeout=0.1,
    ) as conn:
        conn.reset_read_buffer()
        conn.write(PREAMBLE + REQ_UD2)
        conn.flush()

        buffer = bytearray()
        started = time.monotonic()
        # The request itself takes about 1.1 s on the wire at 2400 baud 8E1.
        first_byte_deadline = started + first_byte_timeout + len(PREAMBLE + REQ_UD2) / 218
        last_data: float | None = None
        while time.monotonic() - started < max_duration:
            chunk = conn.read(512)
            now = time.monotonic()
            if chunk:
                buffer += chunk
                if _strip_echo(buffer):
                    # Only count data from the meter, not the echo of our own request
                    # (optical heads often see their own transmitter).
                    last_data = now
                if not all_telegrams and _has_telegram(buffer):
                    break
            elif last_data is None:
                if now > first_byte_deadline:
                    break
            elif now - last_data > idle_timeout:
                break
    return bytes(buffer)


def _strip_echo(buffer: bytes | bytearray) -> bytes:
    """Remove an echoed preamble/request from the start of the received data."""
    data = bytes(buffer).lstrip(b"\x00")
    if data.startswith(REQ_UD2):
        data = data[len(REQ_UD2) :]
    return data.lstrip(b"\x00")


def _has_telegram(buffer: bytearray) -> bool:
    return any(frame[6] == CI_RSP_UD_LONG_HEADER for frame in extract_long_frames(bytes(buffer)))


def read_meter(port: str, *, all_telegrams: bool = False) -> MeterReading:
    """Read the meter on ``port`` and return its current values."""
    return parse_readout(read_raw(port, all_telegrams=all_telegrams))
