#!/usr/bin/env python3
"""Read a meter once from the command line, without Home Assistant.

    pip install serialx
    python scripts/read_meter.py /dev/serial/by-id/usb-...
    python scripts/read_meter.py /dev/ttyUSB0 --all --raw

Keep at least one minute between two readouts.
"""

import argparse
from pathlib import Path
import sys

sys.path.insert(
    0, str(Path(__file__).parents[1] / "custom_components" / "ultraheat_mbus")
)

import mbus  # noqa: E402
import serialx  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("port", help="serial port of the optical head")
    parser.add_argument(
        "--all", action="store_true", help="also read the storage telegrams"
    )
    parser.add_argument("--raw", action="store_true", help="print the raw bytes as hex")
    args = parser.parse_args()

    try:
        raw = mbus.read_raw_all_telegrams(args.port) if args.all else mbus.read_raw(args.port)
    except (OSError, serialx.SerialException) as err:
        print(f"error: cannot use {args.port}: {err}", file=sys.stderr)
        return 1
    if args.raw:
        print(raw.hex())
    try:
        reading = mbus.parse_readout(raw)
    except mbus.MbusError as err:
        print(f"error: {err} ({len(raw)} bytes received)", file=sys.stderr)
        return 1

    print(
        f"{reading.manufacturer_name} {reading.identification}, "
        f"version {reading.version}, medium 0x{reading.medium:02x}"
    )
    for name in (
        "heat_energy",
        "volume",
        "power",
        "volume_flow",
        "flow_temperature",
        "return_temperature",
        "temperature_difference",
        "operating_time",
        "error_time",
        "meter_time",
    ):
        print(f"  {name:24} {getattr(reading, name)}")

    for telegram in reading.telegrams:
        print(f"\ntelegram access_number={telegram.access_number}")
        for record in telegram.records:
            print(
                f"  {record.quantity:34} {record.value!s:>22} {record.unit or '':5}"
                f" function={record.function} storage={record.storage}"
                f" tariff={record.tariff} subunit={record.subunit}"
            )
    for frame, error in reading.undecoded:
        print(f"\nundecoded frame ({len(frame)} bytes): {error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
