"""Data update coordinator for the Ultraheat M-Bus integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from functools import partial
import logging
import os
import time

import serialx

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    READ_ALL_TIMEOUT,
    READ_TIMEOUT,
)
from .mbus import MIN_READ_INTERVAL, MbusError, MeterReading, read_meter

_LOGGER = logging.getLogger(__name__)

type UltraheatConfigEntry = ConfigEntry[UltraheatCoordinator]


@dataclass
class _PortState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_read: float | None = None
    last_reading: MeterReading | None = None  # of the last readout, if it succeeded


def _port_state(hass: HomeAssistant, port: str) -> _PortState:
    ports: dict[str, _PortState] = hass.data.setdefault(DOMAIN, {})
    return ports.setdefault(port, _PortState())


def _canonical_port(port: str) -> str:
    """Resolve symlinks such as /dev/serial/by-id/... so aliases share one state."""
    return port if "://" in port else os.path.realpath(port)


async def async_read_meter(
    hass: HomeAssistant, port: str, *, all_telegrams: bool = False
) -> MeterReading:
    """Read the meter, serialised per port and at most once per minute.

    Within a minute after a successful readout its result is returned again. This
    matters right after the config flow, which reads the meter once to identify it:
    Home Assistant waits for the setup of the new entry before it finishes the flow.
    After a failed readout the next one waits for the rest of the minute. A readout
    of all telegrams (with the storage values) always waits and reads the meter.

    The port stays locked until the readout in the executor has actually finished,
    also when waiting for it timed out or was cancelled.
    """
    state = _port_state(hass, await hass.async_add_executor_job(_canonical_port, port))
    await state.lock.acquire()
    try:
        if state.last_read is not None:
            wait = state.last_read + MIN_READ_INTERVAL - time.monotonic()
            if wait > 0 and state.last_reading is not None and not all_telegrams:
                age = MIN_READ_INTERVAL - wait
                _LOGGER.debug("Using the readout of %s from %.0f s ago", port, age)
                state.lock.release()
                return state.last_reading
            if wait > 0:
                _LOGGER.debug("Waiting %.0f s before reading %s again", wait, port)
                await asyncio.sleep(wait)
        job = hass.async_add_executor_job(partial(read_meter, port, all_telegrams=all_telegrams))
    except BaseException:
        state.lock.release()
        raise

    def _release(job: asyncio.Future[MeterReading]) -> None:
        state.last_read = time.monotonic()
        state.last_reading = (
            job.result() if not job.cancelled() and job.exception() is None else None
        )
        state.lock.release()

    job.add_done_callback(_release)
    timeout = READ_ALL_TIMEOUT if all_telegrams else READ_TIMEOUT
    async with asyncio.timeout(timeout.total_seconds()):
        return await asyncio.shield(job)


class UltraheatCoordinator(DataUpdateCoordinator[MeterReading]):
    """Poll the heat meter."""

    config_entry: UltraheatConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: UltraheatConfigEntry) -> None:
        """Initialize the coordinator."""
        interval = config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=interval),
        )
        self.port: str = config_entry.data[CONF_DEVICE]

    async def _async_update_data(self) -> MeterReading:
        """Fetch the current values from the meter."""
        try:
            reading = await async_read_meter(self.hass, self.port)
        except (MbusError, OSError, TimeoutError, serialx.SerialException) as err:
            raise UpdateFailed(f"Error reading heat meter on {self.port}: {err}") from err
        if reading.identification != self.config_entry.unique_id:
            # Another meter answers on this port (meter replaced or head moved). Its
            # values must not continue the statistics of the configured meter.
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="meter_changed",
                translation_placeholders={
                    "port": self.port,
                    "expected": str(self.config_entry.unique_id),
                    "found": reading.identification,
                },
            )
        return reading
