from pathlib import Path

import pytest

from trackiwi import COLUMNS
from trackiwi.client import normalize_epoch, parse_positions

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic-positions.csv"


def test_parses_all_rows_from_fixture():
    rows, skipped = parse_positions(FIXTURE.read_text(encoding="utf-8"))
    assert len(rows) == 3
    assert skipped == 0
    assert len(rows[0]) == len(COLUMNS)


def test_types_are_coerced():
    rows, _ = parse_positions("1001,7,1758000000,120,31.5,-41.5,12,0.0,0,0,-71,9,98,4120\n")
    row = dict(zip(COLUMNS, rows[0], strict=True))
    assert row["id"] == 1001
    assert row["latitude"] == 31.5
    assert isinstance(row["latitude"], float)
    assert row["altitude"] == 12


def test_empty_optional_field_becomes_none():
    rows, _ = parse_positions("1001,7,1758000000,,31.5,-41.5,,,,,,,,\n")
    row = dict(zip(COLUMNS, rows[0], strict=True))
    assert row["fix_timezone"] is None
    assert row["voltage"] is None


def test_short_row_is_skipped_not_fatal():
    text = "1001,7,1758000000,120,31.5,-41.5,12,0.0,0,0,-71,9,98,4120\n1002,7,broken\n"
    rows, skipped = parse_positions(text)
    assert len(rows) == 1
    assert skipped == 1


def test_overlong_row_is_skipped():
    rows, skipped = parse_positions("1," * 20 + "\n")
    assert rows == []
    assert skipped == 1


def test_row_missing_required_field_is_skipped():
    rows, skipped = parse_positions("1001,7,1758000000,120,,-41.5,12,0.0,0,0,-71,9,98,4120\n")
    assert rows == []
    assert skipped == 1


def test_non_numeric_field_is_skipped():
    rows, skipped = parse_positions("x,7,1758000000,120,31.5,-41.5,12,0.0,0,0,-71,9,98,4120\n")
    assert rows == []
    assert skipped == 1


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "Infinity", "NaN"])
def test_non_finite_coordinate_is_skipped(value):
    """A non-finite float is malformed data, not a position.

    `float()` happily accepts "nan"/"inf", and neither the `None` check nor
    SQLite's `NOT NULL` catches them: `inf` reaches the exporters and produces
    schema-invalid GPX and syntactically invalid JSON, while `nan` becomes
    NULL on insert and wedges the sync page forever.
    """
    rows, skipped = parse_positions(
        f"1001,7,1758000000,120,{value},-41.5,12,0.0,0,0,-71,9,98,4120\n"
    )
    assert rows == []
    assert skipped == 1


def test_non_finite_speed_is_skipped():
    rows, skipped = parse_positions("1001,7,1758000000,120,31.5,-41.5,12,inf,0,0,-71,9,98,4120\n")
    assert rows == []
    assert skipped == 1


@pytest.mark.parametrize("fix_at", ["10000000001000000", "-62135596800000000", "0"])
def test_fix_at_outside_the_datetime_range_is_skipped(fix_at):
    """`normalize_epoch` divides by 1000 exactly once, so a microsecond
    timestamp stays ~1000x too large. Without a range check the store accepts
    it and every later export dies on `datetime.fromtimestamp` — not just the
    export of that row, but of any date range that includes it."""
    rows, skipped = parse_positions(f"1001,7,{fix_at},120,31.5,-41.5,12,0.0,0,0,-71,9,98,4120\n")
    assert rows == []
    assert skipped == 1


def test_plausible_fix_at_is_still_accepted():
    rows, skipped = parse_positions("1001,7,1758000000,120,31.5,-41.5,12,0.0,0,0,-71,9,98,4120\n")
    assert len(rows) == 1
    assert skipped == 0


def test_blank_body_yields_nothing():
    assert parse_positions("") == ([], 0)
    assert parse_positions("\n\n") == ([], 0)


def test_milliseconds_are_normalised_to_seconds():
    assert normalize_epoch(1758000000) == 1758000000
    assert normalize_epoch(1758000000123) == 1758000000


def test_fix_at_is_normalised_during_parse():
    rows, _ = parse_positions("1,7,1758000000123,120,31.5,-41.5,1,0.0,0,0,-71,9,98,4120\n")
    assert dict(zip(COLUMNS, rows[0], strict=True))["fix_at"] == 1758000000
