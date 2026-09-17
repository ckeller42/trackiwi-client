"""Convert position rows into standard GPS formats.

Pure: rows in, text out. No I/O. `fix_at` is already epoch seconds by the time
rows reach here, normalised at parse time.
"""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from . import COLUMNS, __version__

GPX_NS = "http://www.topografix.com/GPX/1/1"


def _iso(fix_at: int) -> str:
    return datetime.fromtimestamp(fix_at, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _by_tracker(rows: Sequence) -> dict:
    """Group rows by `tracker_id`, keeping each tracker's rows in input order.

    An export without `--tracker` covers every tracker (design spec, section 6)
    and `store.query()` orders by `fix_at, id`, so several devices' rows arrive
    interleaved. Merging them into one track or one LineString would produce a
    geometry that teleports between devices on every other point, so each
    tracker gets its own track / feature. Group order is first appearance —
    i.e. earliest fix — which keeps the output deterministic.
    """
    grouped: dict = {}
    for row in rows:
        grouped.setdefault(row["tracker_id"], []).append(row)
    return grouped


def to_gpx(rows: Sequence) -> str:
    """Render rows as GPX 1.1 with one `<trk>` per tracker."""
    gpx = ET.Element(
        "gpx",
        {"version": "1.1", "creator": f"trackiwi-client/{__version__}", "xmlns": GPX_NS},
    )
    for tracker_id, tracker_rows in _by_tracker(rows).items():
        trk = ET.SubElement(gpx, "trk")
        ET.SubElement(trk, "name").text = f"tracker {tracker_id}"
        seg = ET.SubElement(trk, "trkseg")
        for row in tracker_rows:
            point = ET.SubElement(
                seg,
                "trkpt",
                {"lat": f"{row['latitude']:.6f}", "lon": f"{row['longitude']:.6f}"},
            )
            if row["altitude"] is not None:
                ET.SubElement(point, "ele").text = str(row["altitude"])
            ET.SubElement(point, "time").text = _iso(row["fix_at"])
    body = ET.tostring(gpx, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'


def to_geojson(rows: Sequence) -> str:
    """Render rows as a FeatureCollection with one Feature per tracker.

    A tracker with a single fix becomes a `Point`, one with several a
    `LineString`. Each Feature carries its `tracker_id` in `properties` so a
    consumer can tell the devices apart — GPX and GeoJSON used to drop it
    entirely, which made a multi-tracker export impossible to split up again.
    """
    features = []
    for tracker_id, tracker_rows in _by_tracker(rows).items():
        coordinates = [[row["longitude"], row["latitude"]] for row in tracker_rows]
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


def to_csv(rows: Sequence) -> str:
    """Render rows as CSV with a header, in :data:`trackiwi.COLUMNS` order."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([row[column] for column in COLUMNS])
    return buffer.getvalue()


FORMATS: dict[str, Callable[[Sequence], str]] = {
    "gpx": to_gpx,
    "geojson": to_geojson,
    "csv": to_csv,
}
