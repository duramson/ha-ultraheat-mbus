"""Constants for the Ultraheat M-Bus integration."""

from datetime import timedelta

DOMAIN = "ultraheat_mbus"

CONF_SCAN_INTERVAL = "scan_interval"
CONF_HISTORY_IMPORTED = "history_imported"

DEFAULT_SCAN_INTERVAL = 15  # minutes
MIN_SCAN_INTERVAL = 2  # minutes; at least one minute has to pass between readouts
MAX_SCAN_INTERVAL = 1440  # minutes

READ_TIMEOUT = timedelta(seconds=30)
READ_ALL_TIMEOUT = timedelta(seconds=90)  # all telegrams take about 20 s
