"""Read-only client for a personal trackiwi GPS account.

Implements :need:`REQ_ZERO_DEPS` (standard library only, no runtime
dependencies), :need:`REQ_READONLY` (the only state-changing request is
``DELETE /api/v2/session``) and :need:`REQ_NO_PRIVATE_DATA` (no private data is
ever committed). Requirements live as ``sphinx-needs`` objects under ``docs/``;
see ``docs/requirements.rst``.
"""

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
