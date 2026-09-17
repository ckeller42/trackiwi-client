"""Network access to the trackiwi API.

This is the only module that performs network I/O. It knows nothing about
SQLite or output formats.
"""

from __future__ import annotations

from . import COLUMNS

_FLOAT_COLUMNS = {"latitude", "longitude", "speed"}
_REQUIRED_COLUMNS = ("id", "tracker_id", "fix_at", "latitude", "longitude")

#: Beyond this, a value cannot be epoch seconds (it would be year 2286+), so
#: it must be milliseconds. The unit is not documented by trackiwi; see the
#: design spec, section 10.5.
_MILLISECOND_THRESHOLD = 10_000_000_000


class TrackiwiError(Exception):
    """Any failure talking to trackiwi."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class AuthError(TrackiwiError):
    """Credentials were rejected, or the session expired."""


def normalize_epoch(value: int) -> int:
    """Return `value` as epoch seconds, accepting seconds or milliseconds."""
    return value // 1000 if value > _MILLISECOND_THRESHOLD else value


def _coerce(column: str, raw: str) -> float | int | None:
    raw = raw.strip()
    if raw == "":
        return None
    return float(raw) if column in _FLOAT_COLUMNS else int(raw)


def parse_positions(text: str) -> tuple[list[tuple], int]:
    """Parse the sync endpoint's CSV body.

    Returns `(rows, skipped)`. Rows are tuples in :data:`trackiwi.COLUMNS`
    order with `fix_at` normalised to epoch seconds. Malformed rows are
    skipped and counted rather than aborting the batch, matching the vendor
    client's behaviour: one bad row must not discard a whole sync page.
    """
    rows: list[tuple] = []
    skipped = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = line.split(",")
        if len(fields) != len(COLUMNS):
            skipped += 1
            continue
        try:
            values = {c: _coerce(c, f) for c, f in zip(COLUMNS, fields, strict=True)}
        except ValueError:
            skipped += 1
            continue
        if any(values[c] is None for c in _REQUIRED_COLUMNS):
            skipped += 1
            continue
        values["fix_at"] = normalize_epoch(int(values["fix_at"]))
        rows.append(tuple(values[c] for c in COLUMNS))
    return rows, skipped
