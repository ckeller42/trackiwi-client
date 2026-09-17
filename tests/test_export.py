import json
import xml.etree.ElementTree as ET

from trackiwi import COLUMNS
from trackiwi.export import FORMATS, to_csv, to_geojson, to_gpx


def row(pos_id=1, fix_at=1758000000, lat=31.0, lon=-41.0, altitude=12):
    values = (pos_id, 7, fix_at, 120, lat, lon, altitude, 0.0, 0, 0, -71, 9, 98, 4120)
    return dict(zip(COLUMNS, values, strict=True))


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


def test_formats_registry_exposes_all_three():
    assert set(FORMATS) == {"gpx", "geojson", "csv"}
