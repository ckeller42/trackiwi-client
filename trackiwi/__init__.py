"""Read-only client for a personal trackiwi GPS account."""

__version__ = "0.1.0"


class TrackiwiError(Exception):
    """Any failure talking to trackiwi."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class AuthError(TrackiwiError):
    """Credentials were rejected, or the session expired."""


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
