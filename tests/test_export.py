import json
import xml.etree.ElementTree as ET

import pytest

from trackiwi import COLUMNS
from trackiwi.export import FORMATS, to_csv, to_geojson, to_gpx


def row(pos_id=1, fix_at=1758000000, lat=31.0, lon=-41.0, altitude=12, tracker_id=7):
    values = (pos_id, tracker_id, fix_at, 120, lat, lon, altitude, 0.0, 0, 0, -71, 9, 98, 4120)
    return dict(zip(COLUMNS, values, strict=True))


def two_tracker_rows():
    """Rows from two devices, interleaved in time — exactly what `store.query()`
    returns for an export without `--tracker` (it orders by `fix_at, id`)."""
    return [
        row(1, fix_at=1758000000, lat=31.0, lon=-41.0, tracker_id=101),
        row(2, fix_at=1758000010, lat=20.0, lon=-80.0, tracker_id=202),
        row(3, fix_at=1758000020, lat=31.001, lon=-41.0, tracker_id=101),
        row(4, fix_at=1758000030, lat=20.001, lon=-80.0, tracker_id=202),
    ]


def test_gpx_is_well_formed_with_points():
    xml = to_gpx([row(1), row(2, fix_at=1758000060, lat=31.001)])
    tree = ET.fromstring(xml)
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    points = tree.findall(".//g:trkpt", ns)
    assert len(points) == 2
    assert points[0].attrib["lat"] == "31.000000"
    assert points[0].attrib["lon"] == "-41.000000"


def test_gpx_writes_utc_timestamp_and_elevation():
    xml = to_gpx([row(fix_at=1758000000)])
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    tree = ET.fromstring(xml)
    assert tree.find(".//g:trkpt/g:time", ns).text.endswith("Z")
    assert tree.find(".//g:trkpt/g:ele", ns).text == "12"


def test_gpx_omits_elevation_when_absent():
    xml = to_gpx([row(altitude=None)])
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    assert ET.fromstring(xml).find(".//g:trkpt/g:ele", ns) is None


def test_gpx_with_no_rows_is_still_valid():
    ET.fromstring(to_gpx([]))


def test_geojson_with_no_rows_has_no_features():
    assert json.loads(to_geojson([]))["features"] == []


def test_geojson_single_row_is_a_point():
    data = json.loads(to_geojson([row(1, lat=31.0, lon=-41.0)]))
    assert len(data["features"]) == 1
    geometry = data["features"][0]["geometry"]
    assert geometry["type"] == "Point"
    assert geometry["coordinates"] == [-41.0, 31.0]
    properties = data["features"][0]["properties"]
    assert properties["point_count"] == 1
    assert properties["start_time"] == properties["end_time"]


def test_geojson_is_a_linestring_in_lon_lat_order():
    data = json.loads(to_geojson([row(1), row(2, lon=-41.5)]))
    geometry = data["features"][0]["geometry"]
    assert geometry["type"] == "LineString"
    assert geometry["coordinates"][0] == [-41.0, 31.0]
    assert geometry["coordinates"][1][0] == -41.5


def test_csv_has_header_and_rows():
    lines = to_csv([row(1), row(2)]).strip().splitlines()
    assert lines[0] == ",".join(COLUMNS)
    assert len(lines) == 3


# --- Multi-tracker exports must not fuse two devices into one track ---
#
# `store.query()` without `--tracker` returns every tracker's rows interleaved
# by time (spec section 6: "omitting --tracker exports every tracker"). Putting
# them all in one <trkseg> / one LineString produces a track that teleports
# between devices on every other point, and renders as a plausible-looking
# polyline, so nothing downstream can detect it.


NS = {"g": "http://www.topografix.com/GPX/1/1"}


def test_gpx_emits_one_track_per_tracker():
    tree = ET.fromstring(to_gpx(two_tracker_rows()))
    tracks = tree.findall("g:trk", NS)
    assert len(tracks) == 2
    assert [len(t.findall("g:trkseg/g:trkpt", NS)) for t in tracks] == [2, 2]


def test_gpx_track_points_are_not_interleaved_between_trackers():
    tree = ET.fromstring(to_gpx(two_tracker_rows()))
    per_track = [
        [p.attrib["lon"] for p in t.findall("g:trkseg/g:trkpt", NS)]
        for t in tree.findall("g:trk", NS)
    ]
    assert per_track == [["-41.000000", "-41.000000"], ["-80.000000", "-80.000000"]]


def test_gpx_track_name_identifies_the_tracker():
    tree = ET.fromstring(to_gpx(two_tracker_rows()))
    assert [t.find("g:name", NS).text for t in tree.findall("g:trk", NS)] == [
        "tracker 101",
        "tracker 202",
    ]


def test_gpx_single_tracker_is_still_exactly_one_track_and_segment():
    tree = ET.fromstring(to_gpx([row(1), row(2, fix_at=1758000060)]))
    assert len(tree.findall("g:trk", NS)) == 1
    assert len(tree.findall("g:trk/g:trkseg", NS)) == 1


def test_geojson_emits_one_feature_per_tracker():
    data = json.loads(to_geojson(two_tracker_rows()))
    assert len(data["features"]) == 2
    assert [f["properties"]["tracker_id"] for f in data["features"]] == [101, 202]
    assert [f["geometry"]["coordinates"] for f in data["features"]] == [
        [[-41.0, 31.0], [-41.0, 31.001]],
        [[-80.0, 20.0], [-80.0, 20.001]],
    ]


def test_geojson_feature_times_and_counts_are_per_tracker():
    data = json.loads(to_geojson(two_tracker_rows()))
    first, second = data["features"]
    assert first["properties"]["point_count"] == 2
    assert first["properties"]["start_time"] == "2025-09-16T05:20:00Z"
    assert first["properties"]["end_time"] == "2025-09-16T05:20:20Z"
    assert second["properties"]["start_time"] == "2025-09-16T05:20:10Z"


def test_geojson_geometry_type_is_decided_per_tracker():
    rows = [
        row(1, fix_at=1758000000, tracker_id=101),
        row(2, fix_at=1758000010, tracker_id=202),
        row(3, fix_at=1758000020, lat=31.001, tracker_id=101),
    ]
    data = json.loads(to_geojson(rows))
    assert [f["geometry"]["type"] for f in data["features"]] == ["LineString", "Point"]


def test_geojson_single_tracker_feature_carries_its_tracker_id():
    data = json.loads(to_geojson([row(1)]))
    assert data["features"][0]["properties"]["tracker_id"] == 7


def test_geojson_refuses_to_emit_non_finite_coordinates():
    """`Infinity` is not valid JSON (RFC 8259), so a strict parser rejects the
    whole document. Second layer behind the parse-time finiteness check: a row
    that reached the cache before that check existed must fail loudly here
    rather than produce a file no parser accepts."""
    with pytest.raises(ValueError):
        to_geojson([row(1, lat=float("inf"))])


def test_formats_registry_exposes_all_three():
    assert set(FORMATS) == {"gpx", "geojson", "csv"}


def test_gpx_refuses_to_emit_non_finite_coordinates():
    """`inf` in a cache written before the parse-time finiteness check produced
    `<trkpt lat="inf">` — not a valid `xsd:decimal`, and emitted silently with
    exit 0. GeoJSON already failed loudly; GPX has to as well, because that is
    what `cmd_export`'s boundary conversion claims to catch."""
    with pytest.raises(ValueError, match="non-finite"):
        to_gpx([row(1, lat=float("inf"))])
    with pytest.raises(ValueError, match="non-finite"):
        to_gpx([row(1, lon=float("nan"))])
