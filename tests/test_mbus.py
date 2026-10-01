"""Tests for the M-Bus protocol module (no Home Assistant needed)."""

from datetime import datetime
from pathlib import Path
import struct
import sys
import types

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


@pytest.mark.parametrize(
    ("stream", "message"),
    [
        (mbus.PREAMBLE + mbus.REQ_UD2, "only the echo of the request"),
        (b"", "not even the echo"),
        (mbus.PREAMBLE + mbus.REQ_UD2 + b"\xff\x68\x01", "no valid answer in 3 bytes"),
    ],
)
def test_missing_answer_says_what_arrived(stream: bytes, message: str) -> None:
    with pytest.raises(mbus.NoResponseError, match=message):
        mbus.parse_readout(stream)


def test_landis_gyr_manufacturer_data(stream: bytes) -> None:
    """Firmware version, extension byte and telegram number after DIF 0x1F."""
    telegrams = [mbus.parse_telegram(f) for f in mbus.extract_long_frames(stream)]
    assert telegrams[0].manufacturer_data == bytes.fromhex("2107006201")
    assert telegrams[0].firmware_version == "7.21"
    assert telegrams[0].rolling_frame_optical is True
    assert telegrams[1].manufacturer_data[-1] == 2


def test_other_manufacturer_data_is_not_interpreted(stream: bytes) -> None:
    frame = mbus.extract_long_frames(stream)[0]
    telegram = mbus.parse_telegram(frame)
    other = mbus.Telegram(**{**telegram.__dict__, "manufacturer": "QDS"})
    assert other.firmware_version is None
    assert other.rolling_frame_optical is None


def test_strip_echo() -> None:
    assert mbus._strip_echo(mbus.PREAMBLE + mbus.REQ_UD2) == b""
    assert mbus._strip_echo(mbus.PREAMBLE + mbus.REQ_UD2 + b"\x68\x01") == b"\x68\x01"
    # an incomplete echo of the request is no data from the meter
    assert mbus._strip_echo(mbus.PREAMBLE + mbus.REQ_UD2[:4]) == b""


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


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


class _FakeSerial:
    """Replays chunks at given (virtual) times; each empty read takes 0.1 s."""

    def __init__(self, clock: _Clock, chunks: list[tuple[float, bytes]]) -> None:
        self._clock = clock
        self._chunks = list(chunks)
        self.written = b""

    def __enter__(self) -> _FakeSerial:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def reset_read_buffer(self) -> None:
        pass

    def write(self, data: bytes) -> int:
        self.written += data
        return len(data)

    def read(self, size: int) -> bytes:
        if self._chunks and self._chunks[0][0] <= self._clock.now:
            return self._chunks.pop(0)[1][:size]
        self._clock.now += 0.1
        return b""


@pytest.fixture(name="fake_port")
def fake_port_fixture(monkeypatch: pytest.MonkeyPatch):
    """Return a function that makes read_raw() read the given chunks."""
    clock = _Clock()
    monkeypatch.setattr(mbus.time, "monotonic", clock.monotonic)

    def install(chunks: list[tuple[float, bytes]]) -> _Clock:
        fake = types.SimpleNamespace(
            serial_for_url=lambda *args, **kwargs: _FakeSerial(clock, chunks),
            Parity=types.SimpleNamespace(EVEN="even"),
            StopBits=types.SimpleNamespace(ONE=1),
        )
        monkeypatch.setitem(sys.modules, "serialx", fake)
        return clock

    return install


ECHO = mbus.PREAMBLE + mbus.REQ_UD2


@pytest.mark.parametrize("split", range(1, len(ECHO)))
def test_read_raw_split_echo(fake_port, stream: bytes, split: int) -> None:
    """A split echo must not shorten the time the meter has to answer."""
    frame = mbus.extract_long_frames(stream)[0]
    fake_port([(0.0, ECHO[:split]), (0.05, ECHO[split:]), (1.2, frame)])
    reading = mbus.parse_readout(mbus.read_raw("/dev/null"))
    assert reading.heat_energy == 143


def test_read_raw_late_answer(fake_port, stream: bytes) -> None:
    frame = mbus.extract_long_frames(stream)[0]
    fake_port([(0.0, ECHO), (3.5, frame)])
    assert mbus.parse_readout(mbus.read_raw("/dev/null")).heat_energy == 143


def test_read_raw_noise_before_answer(fake_port, stream: bytes) -> None:
    frame = mbus.extract_long_frames(stream)[0]
    fake_port([(0.0, ECHO), (0.5, b"\xff"), (2.5, frame)])
    assert mbus.parse_readout(mbus.read_raw("/dev/null")).heat_energy == 143


def test_read_raw_echo_only_waits_for_deadline(fake_port) -> None:
    clock = fake_port([(0.0, ECHO)])
    raw = mbus.read_raw("/dev/null")
    assert raw == ECHO
    assert clock.now >= 3.0
    with pytest.raises(mbus.NoResponseError):
        mbus.parse_readout(raw)


def test_send_command_needs_acknowledgement(fake_port) -> None:
    command = mbus.APP_RESET_FIRST_ONLY
    fake_port([(0.0, mbus.PREAMBLE + command), (1.2, bytes([mbus.ACK]))])
    mbus.send_command("/dev/null", command)


def test_send_command_without_answer(fake_port) -> None:
    command = mbus.APP_RESET_FIRST_ONLY
    fake_port([(0.0, mbus.PREAMBLE + command)])
    with pytest.raises(mbus.NoResponseError, match="only the echo"):
        mbus.send_command("/dev/null", command)


@pytest.fixture(name="meter")
def meter_fixture(monkeypatch: pytest.MonkeyPatch, stream: bytes) -> list[bytes]:
    """Answer commands with an acknowledgement and requests with the recorded readout."""
    sent: list[bytes] = []

    def read_raw(port: str, *, request: bytes = mbus.REQ_UD2, **kwargs: object) -> bytes:
        sent.append(request)
        return stream if request == mbus.REQ_UD2 else request + bytes([mbus.ACK])

    monkeypatch.setattr(mbus, "read_raw", read_raw)
    return sent


def test_read_meter_sends_only_the_request(meter: list[bytes]) -> None:
    mbus.read_meter("/dev/null")
    assert meter == [mbus.REQ_UD2]


def test_read_meter_switches_to_first_telegram(meter: list[bytes]) -> None:
    mbus.read_meter("/dev/null", first_only=True)
    assert meter == [mbus.APP_RESET_FIRST_ONLY, mbus.REQ_UD2]


def test_read_meter_without_acknowledgement(
    monkeypatch: pytest.MonkeyPatch, stream: bytes
) -> None:
    """A switch to the first telegram that is not acknowledged does not stop the readout."""

    def read_raw(port: str, *, request: bytes = mbus.REQ_UD2, **kwargs: object) -> bytes:
        return stream if request == mbus.REQ_UD2 else request

    monkeypatch.setattr(mbus, "read_raw", read_raw)
    assert mbus.read_meter("/dev/null", first_only=True).heat_energy is not None


def test_read_all_telegrams_switches_rolling_frame(meter: list[bytes]) -> None:
    """All telegrams are only sent with the rolling frame on, so it is switched on and off."""
    reading = mbus.read_meter("/dev/null", all_telegrams=True)
    assert meter == [mbus.APP_RESET_ALL, mbus.REQ_UD2, mbus.APP_RESET_FIRST_ONLY]
    assert reading.history


def test_application_reset_frames() -> None:
    """Checksums as in Landis+Gyr TKB3462 and the forum scripts."""
    assert mbus.APP_RESET_FIRST_ONLY.hex(" ") == "68 03 03 68 53 fe 50 a1 16"
    assert mbus.APP_RESET_ALL.hex(" ") == "68 04 04 68 53 fe 50 00 a1 16"


def test_read_raw_all_telegrams(fake_port, stream: bytes) -> None:
    frames = mbus.extract_long_frames(stream)
    fake_port([(0.0, ECHO), *((1.2 + 0.3 * i, f) for i, f in enumerate(frames))])
    assert len(mbus.extract_long_frames(mbus.read_raw("/dev/null", all_telegrams=True))) == 7


def test_all_frames_decode(stream: bytes) -> None:
    frames = mbus.extract_long_frames(stream)
    telegrams = [mbus.parse_telegram(frame) for frame in frames]
    assert [t.access_number for t in telegrams] == list(range(7))


def test_plain_text_vif(stream: bytes) -> None:
    """The second telegram has plain text VIFs with a VIFE before the length byte."""
    telegram = mbus.parse_telegram(mbus.extract_long_frames(stream)[1])
    texts = [r.unit for r in telegram.records if r.quantity == "plain_text"]
    assert texts == ["FT", "FS", "FE", "FF1", "FF2"]
    assert telegram.records[-1].vif == 0xFD
    assert telegram.records[-1].value == 77450  # model / version


def test_frame_behind_bogus_length_header(stream: bytes) -> None:
    frame = mbus.extract_long_frames(stream)[0]
    assert mbus.extract_long_frames(bytes.fromhex("68 ff ff 68") + frame) == [frame]


def test_short_frames_are_ignored() -> None:
    assert mbus.extract_long_frames(bytes.fromhex("68 00 00 68 00 16")) == []
    assert mbus.extract_long_frames(bytes.fromhex("68 00 00 68 00 16 00 00 00")) == []


@pytest.mark.parametrize(
    "data",
    [
        "01 7c",  # plain text VIF without length
        "01 7c 05 41",  # plain text VIF shorter than its length
        "0d 13",  # LVAR missing
        "0d 13 05 41",  # LVAR data shorter than announced
        "0d 13 f8",  # reserved LVAR
        "84",  # DIFE missing
        "04 93",  # VIFE missing
        "04 13 01",  # data field too short
    ],
)
def test_truncated_records(data: str) -> None:
    with pytest.raises(mbus.InvalidFrameError):
        mbus.parse_records(bytes.fromhex(data))


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ("0d 13 c2 34 12", 1.234),  # positive BCD, 4 digits
        ("0d 13 d1 12", -0.012),  # negative BCD, 2 digits
        ("0d 13 e2 34 12", 4.66),  # binary, 2 bytes
    ],
)
def test_lvar_numbers(data: str, expected: float) -> None:
    records, _ = mbus.parse_records(bytes.fromhex(data))
    assert records[0].quantity == "volume"
    assert records[0].value == expected


def test_lvar_ascii() -> None:
    records, _ = mbus.parse_records(bytes.fromhex("0d fd 11 03 43 42 41"))
    assert records[0].value == "ABC"


def test_float_keeps_precision() -> None:
    records, _ = mbus.parse_records(bytes.fromhex("05 03") + struct.pack("<f", 1.25))
    assert records[0].quantity == "energy"
    assert records[0].value == pytest.approx(0.00125)


def test_storage_telegrams_are_no_reading(stream: bytes) -> None:
    """Without the telegram with the current values there is no reading."""
    frames = mbus.extract_long_frames(stream)
    broken = bytearray(frames[0])
    broken[-2] ^= 0xFF
    with pytest.raises(mbus.InvalidFrameError):
        mbus.parse_readout(bytes(broken) + b"".join(frames[1:]))


def test_current_values_need_not_come_first(stream: bytes) -> None:
    frames = mbus.extract_long_frames(stream)
    reading = mbus.parse_readout(frames[1] + frames[0])
    assert reading.heat_energy == 143
    assert reading.telegrams[0].access_number == 1


def test_undecoded_frames_are_kept(stream: bytes) -> None:
    frames = mbus.extract_long_frames(stream)
    # A valid frame with an unsupported CI field.
    body = bytes([0x08, 0xFE, 0x78, 0x01])
    other = bytes([0x68, len(body), len(body), 0x68]) + body + bytes([sum(body) & 0xFF, 0x16])
    reading = mbus.parse_readout(frames[0] + other)
    assert reading.heat_energy == 143
    assert reading.undecoded == [(other, "frame too short")]


def test_redact_frame(stream: bytes) -> None:
    """Redacted telegrams contain no identifying numbers and can still be parsed."""
    frames = mbus.extract_long_frames(stream)
    redacted = [mbus.redact_frame(frame) for frame in frames]

    assert mbus.extract_long_frames(b"".join(redacted)) == redacted
    for frame in redacted:
        for needle in ("12345678", "87654321"):
            bcd = bytes.fromhex(needle)[::-1]
            assert bcd not in frame
            assert needle.encode() not in frame

    reading = mbus.parse_readout(b"".join(redacted))
    assert reading.identification == "00000000"
    assert reading.fabrication_number == "00000000"
    assert reading.heat_energy == 143
    first = mbus.parse_telegram(frames[0])
    assert [r.quantity for r in first.records if mbus.is_identifying(r)] == [
        "fabrication_number"
    ]


def test_storage_history(stream: bytes) -> None:
    history = mbus.parse_readout(stream).history
    assert [(h.time, h.heat_energy, h.volume) for h in history] == [
        (datetime(2025, 12, 31, 23, 59), 0, 0.0),
        (datetime(2026, 5, 31, 23, 59), 66, 4.87),
        (datetime(2026, 6, 30, 23, 59), 86, 7.64),
        (datetime(2026, 7, 31, 23, 59), 102, 10.12),
        (datetime(2026, 8, 31, 23, 59), 123, 12.27),
    ]


def test_storage_history_needs_dates(stream: bytes) -> None:
    """Without the storage telegrams there is no dated history."""
    first = mbus.extract_long_frames(stream)[0]
    assert mbus.parse_readout(first).history == []
