"""Convert position rows into standard GPS formats.

Pure: rows in, text out. No I/O. `fix_at` is already epoch seconds by the time
rows reach here, normalised at parse time.
"""

from __future__ import annotations

import csv
import io
import json
import math
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from . import COLUMNS, __version__

GPX_NS = "http://www.topografix.com/GPX/1/1"


class Row(Protocol):
    """A position row addressed by column name.

    The exporters accept both a `sqlite3.Row` (what `store.query` returns) and a
    plain `dict` (what the doctests and callers build); both answer `row["name"]`
    for a column, so that is all this contract promises. Values are `Any` because
    the cache stores heterogeneous columns (ints, floats, `None`).
    """

    def __getitem__(self, key: str) -> Any: ...


def _iso(fix_at: int) -> str:
    return datetime.fromtimestamp(fix_at, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _by_tracker(rows: Sequence[Row]) -> dict[Any, list[Row]]:
    """Group rows by `tracker_id`, keeping each tracker's rows in input order.

    An export without `--tracker` covers every tracker (design spec, section 6)
    and `store.query()` orders by `fix_at, id`, so several devices' rows arrive
    interleaved. Merging them into one track or one LineString would produce a
    geometry that teleports between devices on every other point, so each
    tracker gets its own track / feature. Group order is first appearance —
    i.e. earliest fix — which keeps the output deterministic.
    """
    grouped: dict[Any, list[Row]] = {}
    for row in rows:
        grouped.setdefault(row["tracker_id"], []).append(row)
    return grouped


def _finite(row: Row) -> tuple[float, float]:
    """Return `(latitude, longitude)`, rejecting a non-finite pair.

    Implements :need:`REQ_GEOJSON_VALID` and :need:`REQ_MALFORMED_SKIP` (the
    export-side second layer for rows cached before parse-time checks existed).

    A coordinate is checked at parse time, so this only ever fires for a row
    that reached the cache *before* that check existed — a real population,
    since the client has been runnable throughout. `inf` is not a valid
    `xsd:decimal`, so `f"{inf:.6f}"` produced `lat="inf"`: schema-invalid GPX,
    written silently with exit 0. GeoJSON already refused such a row
    (`allow_nan=False`), and `cmd_export` converts the `ValueError` into a
    clean message, which is what its own comment claims for both formats.
    """
    latitude, longitude = row["latitude"], row["longitude"]
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        raise ValueError(f"non-finite coordinate in cached row {row['id']}")
    return latitude, longitude


def to_gpx(rows: Sequence[Row]) -> str:
    """Render rows as GPX 1.1 with one `<trk>` per tracker.

    Implements :need:`REQ_EXPORT_PER_TRACKER`.

    >>> rows = [
    ...     {"id": 1, "tracker_id": 7, "fix_at": 1700000000,
    ...      "latitude": 31.0, "longitude": -41.0, "altitude": None},
    ... ]
    >>> gpx = to_gpx(rows)
    >>> '<trk>' in gpx and 'tracker 7' in gpx
    True
    >>> 'lat="31.000000"' in gpx and 'lon="-41.000000"' in gpx
    True
    """
    gpx = ET.Element(
        "gpx",
        {"version": "1.1", "creator": f"trackiwi-client/{__version__}", "xmlns": GPX_NS},
    )
    for tracker_id, tracker_rows in _by_tracker(rows).items():
        trk = ET.SubElement(gpx, "trk")
        ET.SubElement(trk, "name").text = f"tracker {tracker_id}"
        seg = ET.SubElement(trk, "trkseg")
        for row in tracker_rows:
            latitude, longitude = _finite(row)
            point = ET.SubElement(
                seg, "trkpt", {"lat": f"{latitude:.6f}", "lon": f"{longitude:.6f}"}
            )
            if row["altitude"] is not None:
                ET.SubElement(point, "ele").text = str(row["altitude"])
            ET.SubElement(point, "time").text = _iso(row["fix_at"])
    body = ET.tostring(gpx, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'


def to_geojson(rows: Sequence[Row]) -> str:
    """Render rows as a FeatureCollection with one Feature per tracker.

    Implements :need:`REQ_EXPORT_PER_TRACKER` and :need:`REQ_GEOJSON_VALID`.

    A tracker with a single fix becomes a `Point`, one with several a
    `LineString`. Each Feature carries its `tracker_id` in `properties` so a
    consumer can tell the devices apart — GPX and GeoJSON used to drop it
    entirely, which made a multi-tracker export impossible to split up again.

    >>> import json
    >>> rows = [
    ...     {"id": 1, "tracker_id": 7, "fix_at": 1700000000,
    ...      "latitude": 31.0, "longitude": -41.0},
    ...     {"id": 2, "tracker_id": 7, "fix_at": 1700000060,
    ...      "latitude": 31.5, "longitude": -41.5},
    ... ]
    >>> doc = json.loads(to_geojson(rows))
    >>> doc["type"]
    'FeatureCollection'
    >>> doc["features"][0]["geometry"]["type"]
    'LineString'
    >>> doc["features"][0]["properties"]["tracker_id"]
    7
    """
    features: list[dict[str, Any]] = []
    for tracker_id, tracker_rows in _by_tracker(rows).items():
        coordinates = [[row["longitude"], row["latitude"]] for row in tracker_rows]
        geometry: dict[str, Any]
        if len(coordinates) == 1:
            geometry = {"type": "Point", "coordinates": coordinates[0]}
        else:
            geometry = {"type": "LineString", "coordinates": coordinates}
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "tracker_id": tracker_id,
                    "point_count": len(coordinates),
                    "start_time": _iso(tracker_rows[0]["fix_at"]),
                    "end_time": _iso(tracker_rows[-1]["fix_at"]),
                },
            }
        )
    # allow_nan=False: `Infinity`/`NaN` are not valid JSON (RFC 8259), so a
    # non-finite coordinate must raise here rather than produce a document
    # strict parsers reject wholesale.
    return (
        json.dumps({"type": "FeatureCollection", "features": features}, indent=2, allow_nan=False)
        + "\n"
    )


def to_csv(rows: Sequence[Row]) -> str:
    """Render rows as CSV with a header, in :data:`trackiwi.COLUMNS` order.

    A raw dump of the cache, so unlike GPX/GeoJSON it does not re-validate.

    >>> row = dict(zip(COLUMNS,
    ...     (1, 7, 1700000000, 1, 31.0, -41.0, 12, 0.0, 0, 0, -71, 9, 98, 4120)))
    >>> print(to_csv([row]), end="")
    id,tracker_id,fix_at,fix_timezone,latitude,longitude,altitude,speed,course,distance,rssi,sat,battery,voltage
    1,7,1700000000,1,31.0,-41.0,12,0.0,0,0,-71,9,98,4120
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([row[column] for column in COLUMNS])
    return buffer.getvalue()


FORMATS: dict[str, Callable[[Sequence[Row]], str]] = {
    "gpx": to_gpx,
    "geojson": to_geojson,
    "csv": to_csv,
}
