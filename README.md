# Ultraheat M-Bus

Home Assistant integration for **Landis+Gyr Ultraheat T230 / T330** heat meters and the
same meters sold under other names, such as the **ista ultego III smart** and the
**Qundis Qheat 5.5**. The meter is read through its **optical interface** with an IR
read/write head. The integration speaks M-Bus (EN 13757-3) over the optical port, fully
locally, with no cloud, radio key or additional hardware beyond the IR head.

> **Deutsch:** Liest Wärmezähler (Wärmemengenzähler) vom Typ Landis+Gyr Ultraheat
> T230/T330 über die optische Schnittstelle mit einem IR-Lesekopf (z. B. Hichi) in
> Home Assistant aus. Das umfasst auch Geräte, die unter anderem Namen verbaut werden,
> etwa den **ista ultego III smart** (Typschild `T230-…`) aus der Heizkostenabrechnung.
> Werte: Wärmeenergie (kWh, Energie-Dashboard), Volumen, Leistung, Durchfluss, Vor- und
> Rücklauftemperatur.

The meter model is printed on the type label. An ista ultego III smart with a type
label starting with `T230-` is a Landis+Gyr T230.

The existing core integration [Landis+Gyr Heat Meter](https://www.home-assistant.io/integrations/landisgyr_heat_meter/)
covers the older Ultraheat UH50 / T550, which answer with an IEC 62056-21 text telegram.
The T230 and T330 do not; they answer with binary M-Bus frames, which is what this
integration implements.

## Supported meters

| Meter | Status |
|---|---|
| ista ultego III smart (`u3.0s radio`, type label `T230-…`, article 77450) | single readouts tested, continuous operation under test (see [Limitations](#limitations)) |
| Landis+Gyr Ultraheat T230 | expected to work (same device) |
| Landis+Gyr Ultraheat T330 | expected to work (same optical protocol, reported by others) |
| Qundis Qheat 5.5 | expected to work (reported as identical to T230/T330) |

Reports for other meters are welcome. The diagnostics download contains the raw telegrams
with identification and fabrication numbers zeroed; please attach it to an issue.

## Limitations

- The tested ista meter answers without a button press, also more than an hour after the
  last one. Long-term operation is still being tested. If your meter stops answering, press
  its button and try again; reports on how your meter behaves are welcome.
- The entities are created from the first readout during setup. If the meter answers with
  another identification later (meter replaced or head moved to another meter), updates fail
  until the entry is removed and added again.

## Hardware

Any IR read/write head according to IEC 62056-21 that shows up as a serial port, for example
a Hichi IR USB head or a comparable USB/TTL head. The head sits magnetically on the optical
interface (the round port next to the display, marked with a triangle on ista devices).

When Home Assistant runs in a virtual machine, pass the USB device through to the VM. Many
IR heads use a CP210x or CH340 converter with the vendor's default USB IDs; if another device
with the same IDs is attached (for example a Zigbee coordinator), pass the devices through by
USB port instead of by vendor/product ID.

## Installation

### HACS

1. HACS → ⋮ → *Custom repositories* → add `https://github.com/duramson/ha-ultraheat-mbus` as
   type *Integration*.
2. Install *Ultraheat M-Bus* and restart Home Assistant.

### Manual

Copy `custom_components/ultraheat_mbus` into the `custom_components` folder of your
configuration and restart Home Assistant.

## Configuration

*Settings → Devices & services → Add integration → Ultraheat M-Bus*, then select the serial
port of the IR head. The meter is read once during setup to identify it.

The polling interval defaults to 15 minutes and can be changed in the integration options
(2 to 1440 minutes).

## Entities

| Entity | Unit | Notes |
|---|---|---|
| Heat energy | kWh | `total_increasing`, usable in the energy dashboard |
| Volume | m³ | volume of the heat transfer medium, not drinking water |
| Power | W | |
| Flow rate | m³/h | |
| Flow temperature | °C | |
| Return temperature | °C | |
| Temperature difference | K | |
| Operating time | h | diagnostic |
| Error time | h | diagnostic, time spent in an error state |
| Meter time | – | diagnostic, disabled by default; the meter clock runs on standard time without DST |

Entities for values a meter does not report are not created.

## Polling interval and battery

The T230 technical description specifies more than **one minute between readouts** at
2400 baud for the wired M-Bus connection. It gives no figure for the optical interface
and does not mention a daily limit. The integration applies the same one-minute minimum to
the optical port, per serial port and also between the readout during setup and the first
regular update. Each readout wakes the battery-powered meter, so a moderate interval is
sensible. Users report polling every 30 minutes over several years without battery problems.

## How it works

The optical port is a half-duplex serial line at **2400 baud, 8 data bits, even parity,
1 stop bit**. The meter sleeps and has to be woken before every request:

1. Send a preamble of 240 × `0x00`.
2. Send `REQ_UD2` to the broadcast address, `10 7B FE 79 16`, **immediately** after the
   preamble, in the same write. In tests a pause of about 350 ms, or switching line settings
   between wake-up and request, resulted in no answer at all.
3. The meter answers with a series of `RSP_UD` long frames (`68 L L 68 …  CS 16`, CI `0x72`).
   The first frame contains the current values; its last record (DIF `0x1F`) announces further
   frames with due-date and monthly storage values, which the meter sends on its own right
   after the first one. The integration stops reading once the frame with the current values
   is complete.

Records are decoded according to EN 13757-3 (DIF/DIFE for storage, tariff and function, VIF
for quantity and scaling, BCD, binary, float and variable length data fields, plain text VIFs). Records whose VIF is followed by a
VIFE (for example the time stamps of maxima) are kept separate and not interpreted as the
plain quantity.

The optical head usually receives its own transmission, either directly from its LED or as a
reflection from the meter's window. This echo is expected and filtered out, also when it
arrives split across several reads. It does not indicate that the head is positioned
correctly; only an answer from the meter does.

### Command line

`scripts/read_meter.py` reads a meter without Home Assistant and prints all decoded records,
which helps with positioning the head and with analysing other meters:

```console
pip install serialx
python scripts/read_meter.py /dev/serial/by-id/usb-… --all --raw
```

## Troubleshooting

- **No answer:** check that the head sits centred on the optical port and try rotating it in
  90° steps. Press the button on the meter and try again. Keep at least one minute between
  attempts. Some heads suffer from optical
  crosstalk between their LED and phototransistor; a thin opaque separator between the two has
  helped other users.
- **Checksum errors or partial frames:** ambient light can disturb the receiver; shading the
  head helps.

## References

- Landis+Gyr: *ULTRAHEAT T230 / ULTRACOLD T230 – Technische Beschreibung* (32 18 000 001 f),
  chapter 9 "Kommunikation"; the readout interval is given in 9.1 for the wired M-Bus.
- EN 13757-2 / EN 13757-3 (M-Bus link layer and application layer).
- Photovoltaikforum: [Landis+Gyr ULTRAHEAT T230 Wärmezähler mit TRCT5000 und ESPHome/Wemos D1 Mini auslesen?](https://www.photovoltaikforum.com/thread/188234-landis-gyr-ultraheat-t230-w%C3%A4rmez%C3%A4hler-mit-trct5000-und-esphome-wemos-d1-mini-aus/)
- Akkudoktor forum: [Landis+Gyr ULTRAHEAT T230 Wärmezähler mit Optokopf und Wemos D1 Mini (Tasmota) auslesen?](https://akkudoktor.net/t/landis-gyr-ultraheat-t230-warmezahler-mit-optokopf-und-wemos-d1-mini-tasmota-auslesen/8444)
- kenzodeluxe: [Read values from Landis & Gyr T230/330 / Qundis Qheat 5.5 heat meters](https://gist.github.com/kenzodeluxe/464f2b6b4f810420fabb2f0251b4e913)
- karwho: [landisgyr_t330](https://codeberg.org/karwho/landisgyr_t330) – Perl script for the T330 with MQTT discovery
- vpathuis: [ultraheat](https://github.com/vpathuis/ultraheat) – library behind the core integration for UH50/T550
- ganehag: [pyMeterBus](https://github.com/ganehag/pyMeterBus)

## License

MIT
