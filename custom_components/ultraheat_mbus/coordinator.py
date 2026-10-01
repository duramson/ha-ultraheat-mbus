"""Data update coordinator for the Ultraheat M-Bus integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
import logging
import os
import time

import serialx

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    READ_TIMEOUT,
)
from .mbus import MIN_READ_INTERVAL, MbusError, MeterReading, NoResponseError, read_meter

# Minutes before an interval boundary at which a poll starts. If the meter does not
# answer, a second attempt follows a minute later and still ends before the boundary.
POLL_LEAD = 3

_LOGGER = logging.getLogger(__name__)

type UltraheatConfigEntry = ConfigEntry[UltraheatCoordinator]


class MeterChangedError(UpdateFailed):
    """Another meter than the configured one answers on the port."""


@dataclass
class _PortState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_read: float | None = None
    last_reading: MeterReading | None = None  # of the last readout, if it succeeded
    rolling_frame: bool | None = None  # meter sends all telegrams on every request


def _port_state(hass: HomeAssistant, port: str) -> _PortState:
    ports: dict[str, _PortState] = hass.data.setdefault(DOMAIN, {})
    return ports.setdefault(port, _PortState())


def next_poll(now: datetime, interval: int) -> datetime:
    """Return when to poll next: POLL_LEAD minutes before the end of an interval.

    Intervals are counted from local midnight, so with 60 minutes the meter is read at
    hh:57 and with 15 minutes at hh:12, hh:27, hh:42 and hh:57. The consumption of an
    hour then lands in that hour of the statistics instead of being split between two.
    """
    # Boundaries are wall clock times. Around a change of daylight saving time the
    # wall clock jumps by up to an hour: a time in the skipped hour stands for the
    # same time an hour later (fold 0), and the repeated hour has every time twice
    # (fold 0 and 1). So the candidates cover an hour on either side, and the
    # earliest one that is really still ahead wins.
    minute = now.hour * 60 + now.minute + POLL_LEAD
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    first = max((minute - 60) // interval * interval, interval)
    candidates = (
        (midnight + timedelta(minutes=boundary - POLL_LEAD)).replace(fold=fold)
        for boundary in range(first, minute + interval + 61, interval)
        for fold in (0, 1)
    )
    return min(
        (when for when in candidates if when.timestamp() > now.timestamp()),
        key=datetime.timestamp,
    )


def _canonical_port(port: str) -> str:
    """Resolve symlinks such as /dev/serial/by-id/... so aliases share one state."""
    return port if "://" in port else os.path.realpath(port)


async def async_read_meter(
    hass: HomeAssistant,
    port: str,
    *,
    all_telegrams: bool = False,
    reuse_recent: bool = True,
    status_first: bool = False,
) -> MeterReading:
    """Read the meter, serialised per port and at most once per minute.

    Within a minute after a successful readout its result is returned again. This
    matters right after the config flow, which reads the meter once to identify it:
    Home Assistant waits for the setup of the new entry before it finishes the flow.
    After a failed readout the next one waits for the rest of the minute. A readout
    of all telegrams (with the storage values), and one with ``reuse_recent`` off,
    always waits and reads the meter.

    The port stays locked until the readout in the executor has actually finished,
    also when waiting for it timed out or was cancelled.
    """
    state = _port_state(hass, await hass.async_add_executor_job(_canonical_port, port))
    await state.lock.acquire()
    try:
        if state.last_read is not None:
            wait = state.last_read + MIN_READ_INTERVAL - time.monotonic()
            reuse = reuse_recent and not all_telegrams
            if wait > 0 and state.last_reading is not None and reuse:
                age = MIN_READ_INTERVAL - wait
                _LOGGER.debug("Using the readout of %s from %.0f s ago", port, age)
                state.lock.release()
                return state.last_reading
            if wait > 0:
                _LOGGER.debug("Waiting %.0f s before reading %s again", wait, port)
                await asyncio.sleep(wait)
        job = hass.async_add_executor_job(
            partial(
                read_meter,
                port,
                all_telegrams=all_telegrams,
                first_only=not all_telegrams and state.rolling_frame is True,
                status_first=status_first,
            )
        )
    except BaseException:
        state.lock.release()
        raise

    def _release(job: asyncio.Future[MeterReading]) -> None:
        state.last_read = time.monotonic()
        state.last_reading = (
            job.result() if not job.cancelled() and job.exception() is None else None
        )
        if all_telegrams:
            # Switched on for this readout; whether switching it off worked is unknown.
            state.rolling_frame = True
        elif (reading := state.last_reading) is not None:
            # The flag in the first telegram, or simply what arrived.
            state.rolling_frame = bool(reading.rolling_frame_optical) or (
                reading.manufacturer == "LUG" and reading.frames > 1
            )
        state.lock.release()

    job.add_done_callback(_release)
    async with asyncio.timeout(READ_TIMEOUT.total_seconds()):
        return await asyncio.shield(job)


class UltraheatCoordinator(DataUpdateCoordinator[MeterReading]):
    """Poll the heat meter."""

    config_entry: UltraheatConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: UltraheatConfigEntry) -> None:
        """Initialize the coordinator.

        Polling is scheduled by the coordinator itself (see async_start_polling), so
        that readouts end just before the interval boundaries.
        """
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=None,
        )
        self.port: str = config_entry.data[CONF_DEVICE]
        self.interval: int = config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        self._unsub_poll: CALLBACK_TYPE | None = None
        self._polling = False
        # How the meter answers, for the diagnostics: since when it answers or not.
        self.last_answer: datetime | None = None
        self.failures_in_a_row = 0
        self.counts: dict[str, int] = {
            "answered_directly": 0,
            "no_answer_directly": 0,
            "answered_after_state_request": 0,
            "no_answer_after_state_request": 0,
            "other_errors": 0,
        }

    @callback
    def async_start_polling(self) -> None:
        """Poll at the next boundary and keep doing so until the entry is unloaded."""
        self._polling = True

        @callback
        def _poll(_now: datetime) -> None:
            self._unsub_poll = None
            self.config_entry.async_create_background_task(
                self.hass, self.async_refresh(), "ultraheat_mbus poll"
            )
            self.async_start_polling()

        self._unsub_poll = async_track_point_in_time(
            self.hass, _poll, next_poll(dt_util.now(), self.interval)
        )

    @callback
    def async_stop_polling(self) -> None:
        """Cancel the scheduled poll."""
        if self._unsub_poll:
            self._unsub_poll()
            self._unsub_poll = None

    async def _async_update_data(self) -> MeterReading:
        """Fetch the current values from the meter."""
        since = (
            f"{(dt_util.utcnow() - self.last_answer).total_seconds() / 60:.0f} min"
            if self.last_answer
            else "no answer yet"
        )
        try:
            reading, how = await self._async_read()
        except (MbusError, OSError, TimeoutError, serialx.SerialException) as err:
            self.failures_in_a_row += 1
            _LOGGER.debug(
                "No readout, %d in a row, previous answer: %s", self.failures_in_a_row, since
            )
            raise UpdateFailed(f"Error reading heat meter on {self.port}: {err}") from err
        self.last_answer = dt_util.utcnow()
        self.failures_in_a_row = 0
        if reading.identification != self.config_entry.unique_id:
            # Another meter answers on this port (meter replaced or head moved). Its
            # values must not continue the statistics of the configured meter.
            raise MeterChangedError(
                translation_domain=DOMAIN,
                translation_key="meter_changed",
                translation_placeholders={
                    "port": self.port,
                    "expected": str(self.config_entry.unique_id),
                    "found": reading.identification,
                },
            )
        # The access number counts every telegram the meter sends, also unread ones.
        _LOGGER.debug(
            "Answered %s, previous answer: %s; %d telegrams received, access number %s,"
            " rolling frame %s, meter state %s",
            how,
            since,
            reading.frames,
            reading.access_number,
            reading.rolling_frame_optical,
            reading.meter_state,
        )
        return reading

    async def _async_read(self) -> tuple[MeterReading, str]:
        """Read the meter; a poll asks for the meter state and tries again if silent.

        Some T230 ignore the data request after a while without communication. The
        Landis+Gyr software asks for the meter state first. Asking only after a silent
        first attempt shows in the counts whether that makes the difference. During
        setup there is a single attempt, so that a silent meter does not delay it.
        """
        try:
            # Only the readout during setup may reuse the one of the config flow. A
            # scheduled poll closes an interval, so it reads the meter again.
            reading = await async_read_meter(
                self.hass, self.port, reuse_recent=not self._polling
            )
        except NoResponseError as err:
            self.counts["no_answer_directly"] += 1
            if not self._polling:
                raise
            _LOGGER.debug("No answer (%s); asking for the meter state, then again", err)
        except (MbusError, OSError, TimeoutError, serialx.SerialException):
            self.counts["other_errors"] += 1
            raise
        else:
            self.counts["answered_directly"] += 1
            return reading, "directly"
        try:
            # Waits for the minute between readouts.
            reading = await async_read_meter(
                self.hass, self.port, reuse_recent=False, status_first=True
            )
        except NoResponseError:
            self.counts["no_answer_after_state_request"] += 1
            raise
        except (MbusError, OSError, TimeoutError, serialx.SerialException):
            self.counts["other_errors"] += 1
            raise
        self.counts["answered_after_state_request"] += 1
        return reading, "after the meter state request"
