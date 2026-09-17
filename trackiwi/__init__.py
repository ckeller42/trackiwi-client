"""Read-only client for a personal trackiwi GPS account."""

__version__ = "0.1.0"

#: Column order of the sync CSV and of the local `positions` table.
COLUMNS = (
    "id",
    "tracker_id",
    "fix_at",
    "fix_timezone",
    "latitude",
    "longitude",
    "altitude",
    "speed",
    "course",
    "distance",
    "rssi",
    "sat",
    "battery",
    "voltage",
)
