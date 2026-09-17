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


def to_gpx(rows: Sequence) -> str:
    """Render rows as a single GPX 1.1 track segment."""
    gpx = ET.Element(
        "gpx",
        {"version": "1.1", "creator": f"trackiwi-client/{__version__}", "xmlns": GPX_NS},
    )
    trk = ET.SubElement(gpx, "trk")
    ET.SubElement(trk, "name").text = "trackiwi export"
    seg = ET.SubElement(trk, "trkseg")
    for row in rows:
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
    """Render rows as a FeatureCollection holding one Point or LineString."""
    coordinates = [[row["longitude"], row["latitude"]] for row in rows]
    features = []
    if coordinates:
        if len(coordinates) == 1:
            geometry = {"type": "Point", "coordinates": coordinates[0]}
        else:
            geometry = {"type": "LineString", "coordinates": coordinates}
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "point_count": len(coordinates),
                    "start_time": _iso(rows[0]["fix_at"]),
                    "end_time": _iso(rows[-1]["fix_at"]),
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
