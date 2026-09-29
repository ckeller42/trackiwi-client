from pathlib import Path

import pytest

from trackiwi import COLUMNS
from trackiwi.client import TrackiwiError, epoch_from_iso, normalize_epoch, parse_positions

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic-positions.csv"


def test_parses_all_rows_from_fixture():
    rows, skipped, _ = parse_positions(FIXTURE.read_text(encoding="utf-8"))
    assert len(rows) == 3
    assert skipped == 0
    assert len(rows[0]) == len(COLUMNS)


def test_types_are_coerced():
    rows, _, _ = parse_positions("1001,7,1758000000,120,31.5,-41.5,12,0.0,0,0,-71,9,98,4120\n")
    row = dict(zip(COLUMNS, rows[0], strict=True))
    assert row["id"] == 1001
    assert row["latitude"] == 31.5
    assert isinstance(row["latitude"], float)
    assert row["altitude"] == 12


def test_empty_optional_field_becomes_none():
    rows, _, _ = parse_positions("1001,7,1758000000,,31.5,-41.5,,,,,,,,\n")
    row = dict(zip(COLUMNS, rows[0], strict=True))
    assert row["fix_timezone"] is None
    assert row["voltage"] is None


def test_short_row_is_skipped_not_fatal():
    text = "1001,7,1758000000,120,31.5,-41.5,12,0.0,0,0,-71,9,98,4120\n1002,7,broken\n"
    rows, skipped, _ = parse_positions(text)
    assert len(rows) == 1
    assert skipped == 1


def test_overlong_row_is_skipped():
    rows, skipped, _ = parse_positions("1," * 20 + "\n")
    assert rows == []
    assert skipped == 1


def test_row_missing_required_field_is_skipped():
    rows, skipped, _ = parse_positions("1001,7,1758000000,120,,-41.5,12,0.0,0,0,-71,9,98,4120\n")
    assert rows == []
    assert skipped == 1


def test_non_numeric_field_is_skipped():
    rows, skipped, _ = parse_positions("x,7,1758000000,120,31.5,-41.5,12,0.0,0,0,-71,9,98,4120\n")
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
    rows, skipped, _ = parse_positions(
        f"1001,7,1758000000,120,{value},-41.5,12,0.0,0,0,-71,9,98,4120\n"
    )
    assert rows == []
    assert skipped == 1


def test_non_finite_speed_is_skipped():
    rows, skipped, _ = parse_positions(
        "1001,7,1758000000,120,31.5,-41.5,12,inf,0,0,-71,9,98,4120\n"
    )
    assert rows == []
    assert skipped == 1


@pytest.mark.parametrize("fix_at", ["10000000001000000", "-62135596800000000", "0"])
def test_fix_at_outside_the_datetime_range_is_skipped(fix_at):
    """`normalize_epoch` divides by 1000 exactly once, so a microsecond
    timestamp stays ~1000x too large. Without a range check the store accepts
    it and every later export dies on `datetime.fromtimestamp` — not just the
    export of that row, but of any date range that includes it."""
    rows, skipped, _ = parse_positions(f"1001,7,{fix_at},120,31.5,-41.5,12,0.0,0,0,-71,9,98,4120\n")
    assert rows == []
    assert skipped == 1


def test_plausible_fix_at_is_still_accepted():
    rows, skipped, _ = parse_positions(
        "1001,7,1758000000,120,31.5,-41.5,12,0.0,0,0,-71,9,98,4120\n"
    )
    assert len(rows) == 1
    assert skipped == 0


def test_the_highest_id_counts_malformed_rows_too():
    """REQ_SYNC_PAST_MALFORMED: a skipped row still tells `sync` how far the page went."""
    text = (
        "1001,7,1758000000,120,31.5,-41.5,12,0.0,0,0,-71,9,98,4120\n"
        "1003,7,1758000120,120,nan,-41.5,12,0.0,0,0,-71,9,98,4120\n"
        "1002,7,broken\n"
    )
    rows, skipped, last_id = parse_positions(text)
    assert [row[0] for row in rows] == [1001]
    assert skipped == 2
    assert last_id == 1003


def test_a_row_without_a_readable_id_reports_no_highest_id():
    assert parse_positions("broken,row\n") == ([], 1, None)
    assert parse_positions(",7,1758000000,120,31.5,-41.5,12,0.0,0,0,-71,9,98,4120\n") == (
        [],
        1,
        None,
    )


def test_blank_body_yields_nothing():
    assert parse_positions("") == ([], 0, None)
    assert parse_positions("\n\n") == ([], 0, None)


def test_milliseconds_are_normalised_to_seconds():
    assert normalize_epoch(1758000000) == 1758000000
    assert normalize_epoch(1758000000123) == 1758000000


def test_fix_at_is_normalised_during_parse():
    rows, _, _ = parse_positions("1,7,1758000000123,120,31.5,-41.5,1,0.0,0,0,-71,9,98,4120\n")
    assert dict(zip(COLUMNS, rows[0], strict=True))["fix_at"] == 1758000000


# --- `fix_at` has two types on two endpoints -----------------------------
#
# The sync CSV sends an epoch integer in seconds; `GET /api/v2/trackers`
# sends an ISO 8601 string inside `latest_positionlog` (`received_at` too).
# The two forms get two functions rather than one union-typed one -- see
# `epoch_from_iso`'s own docstring for why.


def test_normalize_epoch_still_only_promises_the_numeric_form():
    """The CSV path's contract is unchanged: int in, epoch seconds out."""
    assert normalize_epoch(1758000000) == 1758000000
    assert normalize_epoch(1758000000123) == 1758000000


def test_epoch_from_iso_accepts_the_trailing_z():
    """Verified on the 3.11 floor, not just on the dev interpreter:
    `datetime.fromisoformat` parses the `Z` suffix from 3.11 onwards."""
    assert epoch_from_iso("2026-09-17T19:19:59Z") == 1789672799


def test_epoch_from_iso_accepts_a_numeric_offset():
    assert epoch_from_iso("2026-09-17T21:19:59+02:00") == 1789672799


def test_epoch_from_iso_treats_a_naive_timestamp_as_utc():
    """A value with no zone must not be read in the machine's local time.

    `datetime.fromisoformat` returns a naive datetime there, and naive
    `.timestamp()` silently applies the local zone -- so the same response
    would decode to a different instant depending on where the client runs,
    and every exported track would shift by the offset.
    """
    assert epoch_from_iso("2026-09-17T19:19:59") == 1789672799


def test_epoch_from_iso_matches_the_exporters_own_rendering():
    """Round-trips against `export._iso`, which is what writes GPX `<time>`.

    If these two ever disagree the error is a constant time shift in the
    output, which renders as a perfectly plausible track.
    """
    from trackiwi.export import _iso

    assert _iso(epoch_from_iso("2026-09-17T19:19:59Z")) == "2026-09-17T19:19:59Z"


@pytest.mark.parametrize(
    "value",
    ["", "not-a-timestamp", "2026-13-45T99:99:99Z", "1766663018", "2026-09-17 19:19:59 CEST"],
)
def test_epoch_from_iso_converts_an_unparseable_value_at_the_boundary(value):
    """Including a bare digit string: a numeric epoch is *not* ISO 8601, and
    silently accepting one here would make the two functions interchangeable
    in a way that defeats keeping them apart."""
    with pytest.raises(TrackiwiError, match="timestamp"):
        epoch_from_iso(value)


@pytest.mark.parametrize("value", [None, 1766663018, 1.5, [], {}])
def test_epoch_from_iso_rejects_a_non_string(value):
    """The field is absent or `null` on a tracker that has never reported, and
    a `TypeError` escaping `main()` as a traceback is the failure mode the
    boundary-conversion rule exists to prevent."""
    with pytest.raises(TrackiwiError, match="timestamp"):
        epoch_from_iso(value)


def test_epoch_from_iso_rejects_a_timestamp_outside_the_exportable_range():
    """Same bound as the CSV path: a `fix_at` the exporters cannot render is
    malformed, and must not reach them (see `_MIN_FIX_AT`/`_MAX_FIX_AT`)."""
    with pytest.raises(TrackiwiError, match="timestamp"):
        epoch_from_iso("1969-12-31T00:00:00Z")
