"""Constants for the Ultraheat M-Bus integration."""

from datetime import timedelta

DOMAIN = "ultraheat_mbus"

CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 15  # minutes
MIN_SCAN_INTERVAL = 2  # minutes; the meter allows one readout per minute
MAX_SCAN_INTERVAL = 1440  # minutes

READ_TIMEOUT = timedelta(seconds=30)
