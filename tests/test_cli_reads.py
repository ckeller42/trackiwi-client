"""CLI subcommands for the read-only list endpoints.

All fixtures synthetic: invented ids, names and coordinates.
"""

import argparse
from typing import cast

import pytest

from trackiwi.cli import build_parser, main

# `(argv command, Client method, a synthetic record, substrings that must show)`
CASES = [
    (
        "tours",
        "tours",
        {
            "color": "#112233",
            "ended_at": "2026-01-02T03:04:05Z",
            "id": 11,
            "name": "Testfahrt",
            "started_at": "2026-01-01T03:04:05Z",
            "tracker_id": 7,
        },
        ["11", "Testfahrt", "7"],
    ),
    (
        "alarms",
        "alarms",
        {
            "acknowledged": False,
            "alarm_type": "geofence",
            "event": {"latitude": 31.0, "longitude": -41.0},
            "id": 41,
            "inserted_at": "2026-01-03T04:05:06Z",
            "tracker_id": 7,
        },
        ["41", "geofence", "7"],
    ),
    (
        "marker-categories",
        "marker_categories",
        {"color": "#445566", "id": 31, "name": "Invented category"},
        ["31", "Invented category"],
    ),
    ("markers", "markers", {"id": 21, "name": "Invented marker"}, ["21", "Invented marker"]),
    ("shares", "shares", {"id": 51, "name": "Invented share"}, ["51", "Invented share"]),
]


def stub_client(method, records):
    """A stand-in for `Client` answering one read-only method.

    `load` is a **classmethod returning a Client**, which the stub has to
    mirror: making it an instance method here produced a confusing `AuthError`
    from an unrelated code path rather than an obvious `TypeError`.
    """

    class StubClient:
        @classmethod
        def load(cls):
            return cls()

        def __getattr__(self, name):
            if name == method:
                return lambda: records
            raise AttributeError(name)

    return StubClient


@pytest.mark.parametrize(("command", "method", "record", "expected"), CASES)
def test_a_read_command_prints_one_line_per_record(
    command, method, record, expected, capsys, monkeypatch
):
    monkeypatch.setattr("trackiwi.cli.Client", stub_client(method, [record, record]))
    assert main([command]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    for fragment in expected:
        assert fragment in lines[0]


@pytest.mark.parametrize(("command", "method", "record", "expected"), CASES)
def test_a_read_command_is_tab_separated(command, method, record, expected, capsys, monkeypatch):
    """Matches the existing `trackers` command's style: tab separated, one row
    per record, no colour -- so the output stays pipeable into `cut`."""
    monkeypatch.setattr("trackiwi.cli.Client", stub_client(method, [record]))
    assert main([command]) == 0
    assert "\t" in capsys.readouterr().out


@pytest.mark.parametrize(("command", "method", "record", "expected"), CASES)
def test_a_read_command_on_an_empty_list_prints_nothing(
    command, method, record, expected, capsys, monkeypatch
):
    monkeypatch.setattr("trackiwi.cli.Client", stub_client(method, []))
    assert main([command]) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(("command", "method", "record", "expected"), CASES)
def test_a_read_command_survives_a_record_missing_every_field(
    command, method, record, expected, capsys, monkeypatch
):
    """`markers` and `shares` have an unverified element shape, and the others
    are an undocumented API with no deprecation policy. A renamed or absent
    field must not turn a listing into a `KeyError` traceback."""
    monkeypatch.setattr("trackiwi.cli.Client", stub_client(method, [{}]))
    assert main([command]) == 0


@pytest.mark.parametrize(("command", "method", "record", "expected"), CASES)
def test_a_read_command_needs_authentication(command, method, record, expected, capsys):
    assert main([command]) == 2
    assert "login" in capsys.readouterr().err


def test_the_alarms_help_warns_that_the_list_is_a_location_history():
    """Each alarm's `event` embeds latitude/longitude, so the listing is itself
    a movement record. Someone reaching for `alarms` to check what fired has
    to be told that before they paste the output anywhere."""
    parser = build_parser()
    # Reaching into argparse internals: `_subparsers` is optional and a subparser
    # action's `choices` is typed as a bare `Iterable | None`, so narrow it to the
    # name->parser map it actually is.
    subparsers = parser._subparsers
    assert subparsers is not None
    choices = cast("dict[str, argparse.ArgumentParser]", subparsers._group_actions[0].choices)
    alarms = choices["alarms"]
    text = f"{alarms.format_help()} {alarms.description or ''}"
    assert "location" in text.lower()


def test_the_alarms_listing_does_not_dump_the_event_coordinates(capsys, monkeypatch):
    """The command prints the fields worth seeing, not the embedded position.

    Printing coordinates by default would put them into terminal scrollback,
    `script` captures and pasted bug reports for anyone merely asking which
    alarms fired.
    """
    record = {
        "acknowledged": False,
        "alarm_type": "geofence",
        "event": {"latitude": 31.5, "longitude": -41.5},
        "id": 41,
        "inserted_at": "2026-01-03T04:05:06Z",
        "tracker_id": 7,
    }
    monkeypatch.setattr("trackiwi.cli.Client", stub_client("alarms", [record]))
    assert main(["alarms"]) == 0
    out = capsys.readouterr().out
    assert "31.5" not in out
    assert "-41.5" not in out


def test_a_read_command_never_touches_the_local_cache(monkeypatch):
    """These are small live reads and are deliberately not cached: the cache
    exists for positions only. A read-only command must also not create it."""
    from trackiwi.store import default_db_path

    for command, method, record, _ in CASES:
        monkeypatch.setattr("trackiwi.cli.Client", stub_client(method, [record]))
        assert main([command]) == 0
    assert not default_db_path().exists()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path
