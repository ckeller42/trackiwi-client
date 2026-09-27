"""Heading estimation: bearing arithmetic, state flag, and the CLI surface.

All fixtures synthetic: invented coordinates, ids and timestamps.
"""

import time

import pytest

from trackiwi import COLUMNS
from trackiwi.cli import main
from trackiwi.heading import (
    DEFAULT_STALE_AFTER,
    STATES,
    Heading,
    estimate_heading,
    estimate_headings,
    initial_bearing,
)
from trackiwi.store import Store

NOW = 1758000000


def fix(fix_at, lat, lon, speed, course, pos_id=None, tracker_id=7):
    """One position as a dict keyed like a cache row."""
    values = (
        pos_id if pos_id is not None else fix_at,
        tracker_id,
        fix_at,
        120,
        lat,
        lon,
        12,
        speed,
        course,
        0,
        -71,
        9,
        98,
        4120,
    )
    return dict(zip(COLUMNS, values, strict=True))


def as_tuple(row):
    """The same fix as the tuple `Store.upsert` takes."""
    return tuple(row[column] for column in COLUMNS)


# A drive heading north-east at a steady 30, then coming to rest.
DRIVE = [
    fix(NOW - 300, 31.000, -41.000, 30.0, 40),
    fix(NOW - 240, 31.001, -41.000, 30.0, 45),
    fix(NOW - 180, 31.002, -40.999, 30.0, 50),
]
PARKED = DRIVE + [
    fix(NOW - 120, 31.002, -40.999, 0.0, 187),
    fix(NOW - 60, 31.002, -40.999, 0.0, 322),
]


# --- bearing arithmetic ---------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ((0.0, 0.0), (1.0, 0.0), 0.0),  # due north
        ((0.0, 0.0), (0.0, 1.0), 90.0),  # due east
        ((1.0, 0.0), (0.0, 0.0), 180.0),  # due south
        ((0.0, 1.0), (0.0, 0.0), 270.0),  # due west
    ],
)
def test_initial_bearing_cardinal_directions(start, end, expected):
    assert initial_bearing(*start, *end) == pytest.approx(expected, abs=1e-9)


def test_initial_bearing_is_normalised_to_a_half_open_interval():
    """`atan2` yields negatives for westward arcs; they land in [0, 360)."""
    west = initial_bearing(10.0, 10.0, 10.0, 9.0)
    assert 0.0 <= west < 360.0
    assert west == pytest.approx(270.0, abs=0.2)
    # A hair west of due north is the floating-point trap: `-tiny % 360.0`
    # evaluates to exactly 360.0, which is outside the promised interval.
    nearly_north = initial_bearing(0.0, 0.0, 1.0, -1e-15)
    assert 0.0 <= nearly_north < 360.0
    for lat1, lon1, lat2, lon2 in [
        (40.0, -74.0, 51.5, -0.1),
        (51.5, -0.1, 40.0, -74.0),
        (-33.9, 151.2, 35.7, 139.7),
        (35.7, 139.7, -33.9, 151.2),
    ]:
        assert 0.0 <= initial_bearing(lat1, lon1, lat2, lon2) < 360.0


def test_initial_bearing_across_the_antimeridian():
    """179.5 → −179.5 is one degree east, not 359 degrees west."""
    assert initial_bearing(0.0, 179.5, 0.0, -179.5) == pytest.approx(90.0)
    assert initial_bearing(0.0, -179.5, 0.0, 179.5) == pytest.approx(270.0)


# --- estimate_heading -----------------------------------------------------


def test_moving_uses_the_current_course():
    heading = estimate_heading(DRIVE, now=NOW)
    assert heading == Heading(50.0, "moving", "course", NOW - 180, None)


def test_parked_after_driving_uses_the_bearing_of_the_last_two_moving_fixes():
    """The two parked fixes carry drifting `course` values (187, 322) that
    must be ignored; the heading is the geometric approach direction."""
    heading = estimate_heading(PARKED, now=NOW)
    expected = initial_bearing(31.001, -41.000, 31.002, -40.999)
    assert heading.state == "freshly_parked"
    assert heading.source == "bearing"
    assert heading.degrees == pytest.approx(expected)
    assert 0.0 < expected < 90.0  # north-east, as the drive was laid out
    assert heading.moved_at == NOW - 180
    assert heading.parked_for == 180


def test_freshly_parked_versus_stale_is_decided_by_the_threshold():
    """Threshold is inclusive: exactly `stale_after` seconds is still fresh."""
    assert estimate_heading(PARKED, now=NOW, stale_after=180).state == "freshly_parked"
    assert estimate_heading(PARKED, now=NOW, stale_after=179).state == "stale"
    # Default threshold: fresh at one hour, stale one second later.
    assert estimate_heading(PARKED, now=NOW - 180 + DEFAULT_STALE_AFTER).state == "freshly_parked"
    stale = estimate_heading(PARKED, now=NOW - 180 + DEFAULT_STALE_AFTER + 1)
    assert stale.state == "stale"
    # A stale estimate still carries the approach heading — less confidence,
    # not less information.
    assert stale.degrees == estimate_heading(PARKED, now=NOW).degrees
    assert stale.parked_for == DEFAULT_STALE_AFTER + 1


def test_the_stale_threshold_defaults_to_the_documented_constant():
    assert DEFAULT_STALE_AFTER == 3600


def test_no_moving_fix_at_all_is_unknown_not_an_error():
    never_moved = [fix(NOW - 60, 31.0, -41.0, 0.0, 12), fix(NOW - 30, 31.0, -41.0, 0.0, 300)]
    assert estimate_heading(never_moved, now=NOW) == Heading(None, "unknown", "none", None, None)
    assert estimate_heading([], now=NOW) == Heading(None, "unknown", "none", None, None)


def test_a_single_moving_fix_falls_back_to_its_course():
    rows = [fix(NOW - 120, 31.0, -41.0, 20.0, 77), fix(NOW - 60, 31.0, -41.0, 0.0, 250)]
    heading = estimate_heading(rows, now=NOW)
    assert heading == Heading(77.0, "freshly_parked", "course", NOW - 120, 120)


def test_two_moving_fixes_at_the_same_spot_fall_back_to_course():
    """A zero-length arc has no bearing; the raw field is the only source left."""
    rows = [
        fix(NOW - 180, 31.0, -41.0, 5.0, 10),
        fix(NOW - 120, 31.0, -41.0, 5.0, 91),
        fix(NOW - 60, 31.0, -41.0, 0.0, 250),
    ]
    assert estimate_heading(rows, now=NOW) == Heading(
        91.0, "freshly_parked", "course", NOW - 120, 120
    )


def test_a_non_finite_cached_coordinate_falls_back_to_course_instead_of_raising():
    """Rows cached before parse-time finiteness checks existed must not crash."""
    rows = [
        fix(NOW - 180, float("inf"), -41.0, 5.0, 10),
        fix(NOW - 120, 31.0, -41.0, 5.0, 91),
        fix(NOW - 60, 31.0, -41.0, 0.0, 250),
    ]
    assert estimate_heading(rows, now=NOW).degrees == 91.0


def test_a_missing_speed_or_course_never_crashes():
    """NULL `speed` is not movement; NULL `course` while moving is a heading of None."""
    rows = [fix(NOW - 120, 31.0, -41.0, None, 40), fix(NOW - 60, 31.0, -41.0, 0.0, 12)]
    assert estimate_heading(rows, now=NOW).state == "unknown"
    moving_without_course = [fix(NOW - 60, 31.0, -41.0, 12.0, None)]
    assert estimate_heading(moving_without_course, now=NOW) == Heading(
        None, "moving", "none", NOW - 60, None
    )


def test_course_is_normalised_into_the_interval():
    """The API documents 0–360; a literal 360 is the same heading as 0."""
    assert estimate_heading([fix(NOW, 31.0, -41.0, 3.0, 360)], now=NOW).degrees == 0.0


def test_now_defaults_to_the_wall_clock(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW + 0.7)
    assert estimate_heading(PARKED).parked_for == 180


def test_states_lists_every_state_in_falling_confidence():
    assert STATES == ("moving", "freshly_parked", "stale", "unknown")


def test_headings_are_estimated_per_tracker():
    """A fix from another device must never become a vehicle's previous position."""
    other = [
        fix(NOW - 250, 20.0, -80.0, 10.0, 180, tracker_id=202),
        fix(NOW - 200, 19.999, -80.0, 10.0, 180, tracker_id=202),
        fix(NOW - 100, 19.999, -80.0, 0.0, 33, tracker_id=202),
    ]
    mixed = sorted(PARKED + other, key=lambda row: row["fix_at"])
    headings = estimate_headings(mixed, now=NOW)
    assert list(headings) == [7, 202]
    assert headings[7].degrees == estimate_heading(PARKED, now=NOW).degrees
    assert headings[202].degrees == pytest.approx(180.0)
    assert headings[202].parked_for == 200


# --- CLI ------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(time, "time", lambda: float(NOW))
    return tmp_path


def seed(rows):
    with Store() as store:
        store.upsert([as_tuple(row) for row in rows])


def test_cli_prints_one_line_per_tracker(capsys):
    seed(PARKED + [fix(NOW - 10, 20.0, -80.0, 12.0, 270, tracker_id=202)])
    assert main(["heading"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    first, second = (line.split("\t") for line in lines)
    assert first[0] == "7"
    assert first[2:4] == ["freshly_parked", "bearing"]
    assert first[4] == "2025-09-16T05:17:00Z"
    assert first[5] == "180"
    assert second == ["202", "270.0", "moving", "course", "2025-09-16T05:19:50Z", "-"]


def test_cli_never_prints_a_coordinate(capsys):
    """The heading says which way the vehicle points, not where it is."""
    seed(PARKED)
    assert main(["heading"]) == 0
    out = capsys.readouterr().out
    assert "31.0" not in out
    assert "41.0" not in out


def test_cli_stale_after_flag_is_honoured(capsys):
    seed(PARKED)
    assert main(["heading", "--stale-after", "60"]) == 0
    assert capsys.readouterr().out.split("\t")[2] == "stale"
    assert main(["heading", "--stale-after", "-1"]) == 1
    assert "--stale-after" in capsys.readouterr().err


def test_cli_tracker_filter_and_unknown_tracker(capsys):
    seed(PARKED)
    assert main(["heading", "--tracker", "7"]) == 0
    assert capsys.readouterr().out.count("\n") == 1
    assert main(["heading", "--tracker", "9"]) == 0
    assert capsys.readouterr().out == "9\t-\tunknown\tnone\t-\t-\n"


def test_cli_without_a_cache_does_not_create_one(capsys):
    from trackiwi.store import default_db_path

    assert main(["heading"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "sync" in captured.err
    assert not default_db_path().exists()
    # With --tracker the honest answer is a row saying unknown.
    assert main(["heading", "--tracker", "7"]) == 0
    assert capsys.readouterr().out.startswith("7\t-\tunknown")
    assert not default_db_path().exists()


def test_cli_help_states_the_caveats(capsys):
    with pytest.raises(SystemExit):
        main(["heading", "--help"])
    text = capsys.readouterr().out.lower()
    assert "reversed" in text
    assert "estimate" in text
    assert "--stale-after" in text
