"""Estimate which way a tracker is pointing from cached positions.

Pure: rows in, an estimate out. No I/O and no network. Everything here is
arithmetic over positions that `sync` has already cached, so it stays inside
the read-only, zero-dependency design.

**Why this exists.** The feed exposes exactly one directional field,
`course`, and it is GPS course-over-ground — the direction of *travel*. It
means something only while the device is moving: on a parked device the raw
value drifts and only rarely matches the way the vehicle actually stands. There
is no compass field, so a stationary heading cannot be sensed; it can only be
*inferred* from the approach.

**Caveats, stated once here and repeated on the CLI.** The parked heading
assumes the vehicle stopped nose-first in its direction of travel. A vehicle
that reversed into its spot points the other way, and nothing in the data
reveals that. Every value from this module is an estimate; GPS alone cannot
determine a stationary vehicle's true heading.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .export import Row, _by_tracker

#: Seconds after the last moving fix at which a parked estimate becomes
#: ``stale``. One hour: on a device that reports while parked the raw
#: ``course`` has usually drifted by then, and a vehicle that has stood for an
#: hour may well have been moved by hand, towed, or re-parked in a way the
#: feed did not catch (a pushed-back trailer, say). The threshold is a
#: judgement about how far to trust an old approach, not a physical constant,
#: so it is a parameter everywhere it is used.
DEFAULT_STALE_AFTER = 3600

State = Literal["moving", "freshly_parked", "stale", "unknown"]
Source = Literal["course", "bearing", "none"]

#: Every value `Heading.state` can take, in the order confidence falls.
STATES: tuple[State, ...] = ("moving", "freshly_parked", "stale", "unknown")


@dataclass(frozen=True)
class Heading:
    """A tracker's estimated orientation with an explicit confidence flag.

    ``degrees`` is clockwise from true north in ``[0, 360)``, or ``None`` when
    nothing supports an estimate. ``state`` is the confidence flag:

    - ``moving``: the last fix has ``speed > 0``; the heading is that fix's
      ``course`` (GPS course-over-ground, reliable while moving).
    - ``freshly_parked``: the last fix is stationary and the last movement was
      no more than ``stale_after`` seconds ago; the heading is the direction
      of approach as the vehicle came to rest.
    - ``stale``: as ``freshly_parked``, but the last movement is older than
      ``stale_after``. The value is still the approach heading, offered with
      less confidence.
    - ``unknown``: no fix, or no fix has ever shown movement, so there is no
      approach to read a heading from.

    ``source`` says where ``degrees`` came from: ``bearing`` is the initial
    great-circle bearing between the last two moving positions (preferred when
    parked, because raw ``course`` is noisy at low speed), ``course`` is the
    raw field, ``none`` goes with ``degrees is None``.

    ``moved_at`` is the ``fix_at`` (epoch seconds) of the last fix with
    ``speed > 0``; ``parked_for`` is how many seconds ago that was, and is
    ``None`` while moving or unknown.
    """

    degrees: float | None
    state: State
    source: Source
    moved_at: int | None
    parked_for: int | None


def initial_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, in ``[0, 360)``.

    Implements :need:`REQ_HEADING_ESTIMATE`.

    Degrees clockwise from true north, on a spherical Earth. The forward
    azimuth at the *start* of the arc is what a vehicle heading is: over the
    few metres between two consecutive fixes the great circle and the rhumb
    line are indistinguishable anyway. Works across the antimeridian because
    the longitude difference goes through ``sin``/``cos`` rather than being
    compared numerically.

    >>> initial_bearing(0.0, 0.0, 1.0, 0.0)
    0.0
    >>> round(initial_bearing(0.0, 0.0, 0.0, 1.0), 6)
    90.0
    >>> round(initial_bearing(1.0, 0.0, 0.0, 0.0), 6)
    180.0
    >>> round(initial_bearing(0.0, 1.0, 0.0, 0.0), 6)
    270.0
    >>> round(initial_bearing(0.0, 179.5, 0.0, -179.5), 6)  # eastwards across 180°
    90.0
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta = math.radians(lon2 - lon1)
    x = math.sin(delta) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta)
    bearing = math.degrees(math.atan2(x, y)) % 360.0
    # `%` maps a negative angle a hair below zero to exactly 360.0 under
    # floating point; the contract is the half-open interval.
    return 0.0 if bearing >= 360.0 else bearing


def _is_moving(row: Row) -> bool:
    """True for a fix whose ``speed`` is a positive number (``None`` is not)."""
    speed = row["speed"]
    return speed is not None and speed > 0


def _course(row: Row) -> tuple[float | None, Source]:
    """Read a fix's raw ``course`` as ``(degrees, source)``; ``None`` if absent."""
    course = row["course"]
    if course is None or not math.isfinite(course):
        return None, "none"
    return float(course) % 360.0, "course"


def _bearing_between(earlier: Row, later: Row) -> float | None:
    """Bearing ``earlier`` → ``later``, or ``None`` when the pair cannot give one.

    Two fixes at the same coordinates have no direction between them, and a
    non-finite coordinate (possible in a row cached before parse-time checks
    existed) would make the trigonometry raise; both fall back to ``None`` so
    the caller can use the raw ``course`` instead.
    """
    points = (earlier["latitude"], earlier["longitude"], later["latitude"], later["longitude"])
    if not all(math.isfinite(value) for value in points):
        return None
    if points[0] == points[2] and points[1] == points[3]:
        return None
    return initial_bearing(*points)


def _approach(moving: Sequence[Row]) -> tuple[float | None, Source]:
    """Heading from the last moving fixes: their bearing, else the last ``course``."""
    if len(moving) >= 2:
        bearing = _bearing_between(moving[-2], moving[-1])
        if bearing is not None:
            return bearing, "bearing"
    return _course(moving[-1])


def estimate_heading(
    rows: Sequence[Row],
    *,
    now: int | None = None,
    stale_after: int = DEFAULT_STALE_AFTER,
) -> Heading:
    """Estimate one tracker's heading from its time-ordered position rows.

    Implements :need:`REQ_HEADING_ESTIMATE` and :need:`REQ_HEADING_STATE`.

    ``rows`` must belong to a single tracker and be ordered by time, as
    `Store.query(tracker_id=...)` returns them; the last row is the current
    fix. ``now`` (epoch seconds, default: the wall clock) and ``stale_after``
    (seconds, default `DEFAULT_STALE_AFTER`) decide ``freshly_parked`` versus
    ``stale``.

    Moving (last ``speed > 0``): the heading is that fix's ``course``. Parked:
    the heading is the initial bearing between the last two fixes that were
    moving — the direction of approach — and only when that bearing is
    undefined (a single moving fix, or two at the same spot) the last moving
    fix's raw ``course``. No moving fix at all is ``unknown``, never an error.

    The parked estimate assumes the vehicle stopped nose-first; a vehicle that
    reversed into its spot points the opposite way and this cannot be told
    from the data. See the module docstring.

    >>> def fix(fix_at, lat, lon, speed, course):
    ...     return {"fix_at": fix_at, "latitude": lat, "longitude": lon,
    ...             "speed": speed, "course": course}
    >>> drive = [fix(1000, 50.0, 8.0, 30.0, 0), fix(1010, 50.001, 8.0, 30.0, 0)]
    >>> estimate_heading(drive, now=1010).state
    'moving'
    >>> parked = drive + [fix(1020, 50.001, 8.0, 0.0, 137)]
    >>> heading = estimate_heading(parked, now=1600)
    >>> heading.degrees, heading.state, heading.source, heading.parked_for
    (0.0, 'freshly_parked', 'bearing', 590)
    >>> estimate_heading(parked, now=1600, stale_after=300).state
    'stale'
    >>> estimate_heading([], now=1600).state
    'unknown'
    """
    if not rows:
        return Heading(None, "unknown", "none", None, None)
    last = rows[-1]
    if _is_moving(last):
        degrees, source = _course(last)
        return Heading(degrees, "moving", source, int(last["fix_at"]), None)
    moving = [row for row in rows if _is_moving(row)]
    if not moving:
        return Heading(None, "unknown", "none", None, None)
    degrees, source = _approach(moving)
    moved_at = int(moving[-1]["fix_at"])
    parked_for = max(0, (int(time.time()) if now is None else now) - moved_at)
    state: State = "freshly_parked" if parked_for <= stale_after else "stale"
    return Heading(degrees, state, source, moved_at, parked_for)


def estimate_headings(
    rows: Sequence[Row],
    *,
    now: int | None = None,
    stale_after: int = DEFAULT_STALE_AFTER,
) -> dict[Any, Heading]:
    """Estimate every tracker's heading from mixed, time-ordered rows.

    Implements :need:`REQ_HEADING_ESTIMATE`.

    Groups by ``tracker_id`` first, because `Store.query()` without a tracker
    filter interleaves devices by time, and a fix from another device must
    never serve as a vehicle's "previous position". Keys follow first
    appearance, i.e. earliest fix.

    >>> rows = [
    ...     {"tracker_id": 1, "fix_at": 100, "latitude": 0.0, "longitude": 0.0,
    ...      "speed": 5.0, "course": 45},
    ...     {"tracker_id": 2, "fix_at": 101, "latitude": 0.0, "longitude": 0.0,
    ...      "speed": 0.0, "course": 200},
    ... ]
    >>> {k: v.state for k, v in estimate_headings(rows, now=200).items()}
    {1: 'moving', 2: 'unknown'}
    """
    return {
        tracker_id: estimate_heading(tracker_rows, now=now, stale_after=stale_after)
        for tracker_id, tracker_rows in _by_tracker(rows).items()
    }
