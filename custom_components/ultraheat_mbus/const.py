"""Constants for the Ultraheat M-Bus integration."""

from datetime import timedelta

DOMAIN = "ultraheat_mbus"

CONF_SCAN_INTERVAL = "scan_interval"
CONF_HISTORY_IMPORTED = "history_imported"
CONF_HISTORY_UNTIL = "history_until"  # sensor key -> first hour recorded by HA

DEFAULT_SCAN_INTERVAL = 15  # minutes
MIN_SCAN_INTERVAL = 2  # minutes; at least one minute has to pass between readouts
MAX_SCAN_INTERVAL = 1440  # minutes

# Up to three wake-ups (switching the rolling frame on and off around a readout) and
# a readout of all telegrams, which takes about 20 s.
READ_TIMEOUT = timedelta(seconds=90)
