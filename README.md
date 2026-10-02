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
| Landis+Gyr Ultraheat T330 | untested; working T330 scripts use a different request sequence with a switch to 9600 baud, which is not implemented yet |
| Qundis Qheat 5.5 | expected to work (reported as identical to T230/T330) |

Reports for other meters are welcome. The diagnostics download contains the raw telegrams
with identification and fabrication numbers zeroed; please attach it to an issue.

## Limitations

- The tested ista meter answers without a button press, also more than an hour after the
  last one. Long-term operation is still being tested. If your meter stops answering, press
  its button and try again; reports on how your meter behaves are welcome.
- The entities are created from the first readout during setup; values the meter reports
  only later get their sensor when they first appear. If the meter answers with another
  identification later (meter replaced or head moved to another meter), updates fail, and
  after a restart the setup stops with an error instead of waking the other meter again
  and again. Remove the entry and add the meter again, or reload it once the head is back.

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
2. Install *Ultraheat M-Bus (Landis+Gyr, ista, Qundis)* and restart Home Assistant.

### Manual

Copy `custom_components/ultraheat_mbus` into the `custom_components` folder of your
configuration and restart Home Assistant.

## Configuration

*Settings → Devices & services → Add integration*, search for *Ultraheat*, *Landis+Gyr* or
*ista* and select *Ultraheat M-Bus (Landis+Gyr, ista, Qundis)*. Then select the serial
port of the IR head. The meter is read once during setup to identify it.

The polling interval defaults to 15 minutes and can be changed in the integration options
(2 to 1440 minutes). Readouts start three minutes before the end of each interval,
counted from midnight: with 60 minutes at hh:57, with 15 minutes at hh:12, hh:27, hh:42 and
hh:57. The consumption of an hour then ends up in that hour of the statistics instead of
being split between two. If the meter does not answer, the integration asks for the meter
state a minute later and reads again, still before the end of the interval.

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

The *Import meter history* button (configuration) reads the meter's history again, see below.

## Meter history

The meter stores its readings at the due date (31 December) and at the end of each month,
up to about two years back. After the first setup the integration reads these values once
and imports them into the long-term statistics of *Heat energy* and *Volume*. The energy
dashboard then shows the months since the meter was installed, not only the time since the
integration was set up.

- Only monthly values exist. The consumption between two of them is spread evenly over the
  days, so the daily and weekly views show an average day instead of one block at the end of
  each month. Monthly and yearly totals are exact; imported days are estimates.
- The consumption recorded by Home Assistant is never changed: only times before the first
  recorded hour are imported. Home Assistant counts the first sum of a statistic as
  consumption, so the imported sums start at 0 and the running total of the recorded hours is
  shifted to continue them (as *Adjust sum* in the developer tools does). The integration
  remembers the first recorded hour, so importing again only replaces the imported values.
- The full readout keeps the meter transmitting for about 20 seconds, so it runs once and
  not on every start. If it fails, it is tried again at the next start. The *Import meter history*
  button starts it manually.
- Costs are not calculated for imported months.

## Polling interval and battery

The T230 technical description specifies more than **one minute between readouts** at
2400 baud for the wired M-Bus connection. It gives no figure for the optical interface
and does not mention a daily limit. The integration applies the same one-minute minimum to
the optical port, per serial port and also between the readout during setup and the first
regular update. Each readout wakes the battery-powered meter, so a moderate interval is
sensible. If the meter does not answer when Home Assistant starts, the integration does not
retry every minute: the entities start unavailable and the next readout follows the
schedule. A user reports reading two T330 every 30 minutes for five years, until their
regular replacement, without battery problems
([Photovoltaikforum, post #89](https://www.photovoltaikforum.com/thread/188234-landis-gyr-ultraheat-t230-w%C3%A4rmez%C3%A4hler-mit-trct5000-und-esphome-wemos-d1-mini-aus/?postID=4232759#post4232759)).

## How it works

The optical port is a half-duplex serial line at **2400 baud, 8 data bits, even parity,
1 stop bit**. The meter sleeps and has to be woken before every request:

1. Send a preamble of 240 × `0x00`.
2. Send `REQ_UD2` to the broadcast address, `10 7B FE 79 16`, **immediately** after the
   preamble, in the same write. In tests a pause of about 350 ms, or switching line settings
   between wake-up and request, resulted in no answer at all.
3. The meter answers with an `RSP_UD` long frame (`68 L L 68 …  CS 16`, CI `0x72`) with the
   current values. If the *rolling frame* of the optical interface is switched on, a T230
   follows it on its own with 27 frames of due-date and monthly storage values, about 3.8 KB
   or 20 seconds of transmitting, even when nobody reads them. The integration always reads
   until the line is idle, so it notices this by the number of frames, and the manufacturer
   specific bytes at the end of each frame also tell whether it is on. In that case it sends
   an application reset without sub-code (`68 03 03 68 53 FE 50 A1 16`, acknowledged with
   `E5`) before the next readout, so that the meter sends only the first frame, which is
   the factory default. For the history import it switches the rolling frame on (sub-code
   `00`) for one readout and off again afterwards (Landis+Gyr TKB3462, section 5.1).
4. After a while without communication a T230 answers neither the data request nor the
   first *Get meter state* (`68 05 05 68 53 FE 51 0F 0F C0 16`), which it otherwise answers
   with its operating mode and firmware versions as text (`Nb+7.21…`) without changing
   anything (TKB3462, section 2). So every readout starts with the state request (step 1
   and 2 with this frame instead of `REQ_UD2`) and repeats it up to ten times, one second
   after each silent attempt, as scripts that read T330 meters for years do. Once the meter
   has answered, the data request follows and is sent up to three times in a row, as the
   Landis+Gyr service software does. Meters that never answer the state request are read
   all the same. The diagnostics count how many state and data requests each readout took.

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

The error message of a failed readout says what was received instead of an answer:

- *nothing received, not even the echo of the request*: most heads receive their own request,
  so check the head, its cable and the serial port.
- *the meter did not answer, only the echo of the request was received*: the head transmits,
  but the meter stayed silent. See below.
- *no valid answer in N bytes*: something answered, but not with a complete telegram. See
  checksum errors below.

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
