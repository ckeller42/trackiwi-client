"""Line-protocol formatting: escaping, units, NULL handling (REQ_LINEPROTOCOL_UNITS)."""

import math

import pytest

from trackiwi.lineprotocol import MEASUREMENT, format_point


def _row(**overrides):
    row = {
        "id": 1,
        "tracker_id": 7,
        "fix_at": 1700000000,
        "fix_timezone": 1,
        "latitude": 31.0,
        "longitude": -41.0,
        "altitude": 12,
        "speed": 0.0,
        "course": 90,
        "distance": 150,
        "rssi": 5,
        "sat": 9,
        "battery": 99,
        "voltage": 1287,
    }
    row.update(overrides)
    return row


def test_full_row_formats_with_natural_units():
    line = format_point(_row(), "Bus")
    assert line == (
        "trackiwi_position,tracker_id=7,tracker_name=Bus "
        "lat=31.0,lon=-41.0,altitude_m=12.0,speed_kmh=0.0,course_deg=90.0,"
        "distance_m=1.5,voltage_v=12.87,battery_pct=99i,satellites=9i,"
        "gnss_quality=5i,fix_flag=1i 1700000000"
    )


def test_measurement_name_is_fixed():
    assert MEASUREMENT == "trackiwi_position"
    assert format_point(_row(), "Bus").startswith("trackiwi_position,")


def test_tag_value_escapes_space_comma_equals():
    line = format_point(_row(), "My Bus, v=2")
    assert ",tracker_name=My\\ Bus\\,\\ v\\=2 " in line


def test_newline_in_tag_value_becomes_space():
    line = format_point(_row(), "My\nBus")
    assert ",tracker_name=My\\ Bus " in line
    assert "\n" not in line


def test_missing_or_empty_name_falls_back_to_tracker_id():
    assert ",tracker_name=7 " in format_point(_row(), None)
    assert ",tracker_name=7 " in format_point(_row(), "")


def test_null_optional_columns_are_omitted():
    row = _row(
        altitude=None,
        speed=None,
        course=None,
        distance=None,
        rssi=None,
        sat=None,
        battery=None,
        voltage=None,
        fix_timezone=None,
    )
    line = format_point(row, "Bus")
    assert line == "trackiwi_position,tracker_id=7,tracker_name=Bus lat=31.0,lon=-41.0 1700000000"
    assert "None" not in line


def test_non_finite_value_is_rejected():
    with pytest.raises(ValueError):
        format_point(_row(latitude=math.inf), "Bus")
    with pytest.raises(ValueError):
        format_point(_row(speed=math.nan), "Bus")


def test_integer_fields_carry_the_i_suffix_and_floats_do_not():
    line = format_point(_row(), "Bus")
    fields = line.split(" ")[1].split(",")
    assert "battery_pct=99i" in fields
    assert "voltage_v=12.87" in fields
