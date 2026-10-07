"""Convert cached position rows to InfluxDB line protocol.

Pure: no I/O. Implements :need:`REQ_LINEPROTOCOL_UNITS` — fields are written
in natural units (distance cm → m, voltage cV → V, per the units verified
against the live API) so every consumer reads metres and volts, and the two
columns trackiwi misnames are renamed for what they are (``rssi`` is a 0–5
GNSS quality scale; ``fix_timezone`` is a 0/1 flag, not a timezone).

>>> row = {"id": 1, "tracker_id": 7, "fix_at": 1700000000, "fix_timezone": 1,
...        "latitude": 31.0, "longitude": -41.0, "altitude": 12, "speed": 0.0,
...        "course": 90, "distance": 150, "rssi": 5, "sat": 9, "battery": 99,
...        "voltage": 1287}
>>> head, fields, timestamp = format_point(row, "Bus").split(" ")
>>> head
'trackiwi_position,tracker_id=7,tracker_name=Bus'
>>> fields.split(",")[:3]
['lat=31.0', 'lon=-41.0', 'altitude_m=12.0']
>>> "distance_m=1.5" in fields, "voltage_v=12.87" in fields, "battery_pct=99i" in fields
(True, True, True)
>>> timestamp
'1700000000'
"""

from __future__ import annotations

import math
from typing import Any

from . import Row

MEASUREMENT = "trackiwi_position"


#: (field name, source column, divisor or None, "f" for float or "i" for integer)
#: Order is the order written; it is fixed so lines are deterministic.
_FIELDS: tuple[tuple[str, str, float | None, str], ...] = (
    ("lat", "latitude", None, "f"),
    ("lon", "longitude", None, "f"),
    ("altitude_m", "altitude", None, "f"),
    ("speed_kmh", "speed", None, "f"),
    ("course_deg", "course", None, "f"),
    ("distance_m", "distance", 100.0, "f"),
    ("voltage_v", "voltage", 100.0, "f"),
    ("battery_pct", "battery", None, "i"),
    ("satellites", "sat", None, "i"),
    ("gnss_quality", "rssi", None, "i"),
    ("fix_flag", "fix_timezone", None, "i"),
)


def _escape_tag(value: str) -> str:
    """Escape a tag key or value: comma, equals sign and space; newline → space."""
    value = value.replace("\r", " ").replace("\n", " ")
    return value.replace(",", "\\,").replace("=", "\\=").replace(" ", "\\ ")


def _format_field(value: Any, kind: str, divisor: float | None) -> str:
    """Format one field value, rejecting non-finite numbers."""
    number = float(value)
    if divisor is not None:
        number = number / divisor
    if not math.isfinite(number):
        raise ValueError(f"non-finite field value: {value!r}")
    if kind == "i":
        return f"{int(number)}i"
    return repr(number)


def format_point(row: Row, tracker_name: str) -> str:
    """Return one line-protocol line for a cached position row.

    `tracker_name` becomes the ``tracker_name`` tag and must not be empty
    (InfluxDB rejects an empty tag value; `mirror` never sends an unnamed
    tracker, :need:`REQ_MIRROR_IDEMPOTENT`). NULL optional columns are omitted
    rather than written. Raises ``ValueError`` for an empty name or a
    non-finite value.
    """
    if not tracker_name:
        raise ValueError("tracker_name must not be empty")
    tracker_id = int(row["tracker_id"])
    tags = f"tracker_id={tracker_id},tracker_name={_escape_tag(tracker_name)}"
    fields = []
    for field, column, divisor, kind in _FIELDS:
        value = row[column]
        if value is None:
            continue
        fields.append(f"{field}={_format_field(value, kind, divisor)}")
    return f"{MEASUREMENT},{tags} {','.join(fields)} {int(row['fix_at'])}"
