"""CLI wiring for InfluxDB: check, push, and ingest (push runs even if sync fails)."""

import pytest
from conftest import FakeOpener

from trackiwi import AuthError, TrackiwiError, cli
from trackiwi.client import Client
from trackiwi.store import Store


def _row(pid, tracker=7):
    return (pid, tracker, 1700000000 + pid, 1, 31.0, -41.0, 12, 0.0, 90, 150, 5, 9, 99, 1287)


def _cache_rows(*rows, names=None):
    """Cache rows with tracker 7 named, as after any earlier successful `ingest`."""
    with Store() as store:
        store.upsert(list(rows))
        store.set_tracker_names({7: "Bus"} if names is None else names)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("TRACKIWI_INFLUX_URL", "http://influx.example.invalid:8086")
    monkeypatch.setenv("TRACKIWI_INFLUX_VERSION", "2")
    monkeypatch.setenv("TRACKIWI_INFLUX_ORG", "home")
    monkeypatch.setenv("TRACKIWI_INFLUX_BUCKET", "trackiwi")
    monkeypatch.setenv("TRACKIWI_INFLUX_TOKEN", "tok")
    return tmp_path


class FakeWriter:
    instances: list["FakeWriter"] = []

    def __init__(self, config, opener=None):
        self.config = config
        self.batches = []
        FakeWriter.instances.append(self)

    def target_key(self):
        return "k"

    def write(self, lines):
        self.batches.append(list(lines))

    def check(self):
        return True, ["InfluxDB 2.x at x", "bucket 'trackiwi' found"]


@pytest.fixture(autouse=True)
def fake_writer(monkeypatch):
    FakeWriter.instances = []
    monkeypatch.setattr("trackiwi.cli.InfluxWriter", FakeWriter)


def _stub_client(
    batches=None, trackers=None, sync_error=None, authenticated=True, trackers_error=None
):
    class Stub:
        @classmethod
        def load(cls):
            return cls()

        @property
        def authenticated(self):
            return authenticated

        def sync(self, offset=None):
            yield from batches or []
            if sync_error:
                raise sync_error

        def trackers(self):
            if trackers_error:
                raise trackers_error
            return trackers or []

    return Stub


def test_check_prints_report_and_exits_zero(env, capsys):
    assert cli.main(["influx", "check"]) == 0
    assert "bucket 'trackiwi' found" in capsys.readouterr().out


def test_check_exits_one_when_not_ok(env, monkeypatch):
    monkeypatch.setattr(FakeWriter, "check", lambda self: (False, ["bucket 'trackiwi' not found"]))
    assert cli.main(["influx", "check"]) == 1


def test_push_without_cache_is_a_noop(env, capsys):
    assert cli.main(["influx", "push"]) == 0
    assert "nothing cached" in capsys.readouterr().out
    assert not (env / "data" / "trackiwi" / "positions.db").exists()


def test_push_sends_cached_rows(env, capsys):
    _cache_rows(_row(1), _row(2))
    assert cli.main(["influx", "push"]) == 0
    assert len(FakeWriter.instances[0].batches[0]) == 2
    assert "pushed 2 positions" in capsys.readouterr().out


def test_push_with_bad_config_exits_one(env, monkeypatch, capsys):
    monkeypatch.delenv("TRACKIWI_INFLUX_URL")
    assert cli.main(["influx", "push"]) == 1
    assert "TRACKIWI_INFLUX_URL" in capsys.readouterr().err


def test_ingest_syncs_names_and_pushes(env, monkeypatch):
    monkeypatch.setattr(
        "trackiwi.cli.Client",
        _stub_client(batches=[([_row(1), _row(2)], 0, 2)], trackers=[{"id": 7, "name": "Bus"}]),
    )
    assert cli.main(["ingest"]) == 0
    lines = FakeWriter.instances[0].batches[0]
    assert len(lines) == 2
    assert ",tracker_name=Bus " in lines[0]


def test_ingest_pushes_cached_rows_even_when_sync_fails(env, monkeypatch, capsys):
    _cache_rows(_row(1))
    monkeypatch.setattr(
        "trackiwi.cli.Client", _stub_client(sync_error=TrackiwiError("network error: down"))
    )
    assert cli.main(["ingest"]) == 1
    assert len(FakeWriter.instances[0].batches[0]) == 1
    assert "network error" in capsys.readouterr().err


def test_ingest_auth_failure_still_pushes_and_exits_two(env, monkeypatch):
    _cache_rows(_row(1))
    monkeypatch.setattr("trackiwi.cli.Client", _stub_client(authenticated=False))
    assert cli.main(["ingest"]) == 2
    assert len(FakeWriter.instances[0].batches[0]) == 1


def test_sync_command_still_works(env, monkeypatch, capsys):
    monkeypatch.setattr("trackiwi.cli.Client", _stub_client(batches=[([_row(1)], 0, 1)]))
    assert cli.main(["sync"]) == 0
    assert "1 new positions" in capsys.readouterr().out


def test_auth_error_type_is_preserved():
    assert issubclass(AuthError, TrackiwiError)


# --- Tracker names must be known before a push (final review I1) ------------


class _NoTrackiwi:
    """`influx push` is offline: any attempt to reach trackiwi fails the test."""

    @classmethod
    def load(cls, *args, **kwargs):
        raise AssertionError("influx push must not contact trackiwi")


def test_push_before_names_are_known_writes_nothing(env, monkeypatch, capsys):
    _cache_rows(_row(1), _row(2), names={})
    monkeypatch.setattr("trackiwi.cli.Client", _NoTrackiwi)
    assert cli.main(["influx", "push"]) == 1
    assert FakeWriter.instances[0].batches == []
    err = capsys.readouterr().err
    assert "no name known for tracker 7" in err
    assert "trackiwi ingest" in err


def test_ingest_whose_name_refresh_fails_pushes_no_id_tags(env, monkeypatch, capsys):
    """The first ingest on a flaky link: sync worked, `trackers()` did not."""
    monkeypatch.setattr(
        "trackiwi.cli.Client",
        _stub_client(
            batches=[([_row(1)], 0, 1)], trackers_error=TrackiwiError("network error: down")
        ),
    )
    assert cli.main(["ingest"]) == 1
    assert FakeWriter.instances[0].batches == []


# --- A tracker without a name must not stall the mirror (issue #16) ---------


@pytest.mark.parametrize("unnamed", [{"id": 8, "name": ""}, {"id": 8, "name": None}, {"id": 8}])
def test_ingest_gives_a_tracker_the_api_does_not_name_a_fallback(env, monkeypatch, unnamed):
    monkeypatch.setattr(
        "trackiwi.cli.Client",
        _stub_client(
            batches=[([_row(1), _row(2, tracker=8), _row(3)], 0, 3)],
            trackers=[{"id": 7, "name": "Bus"}, unnamed],
        ),
    )
    assert cli.main(["ingest"]) == 0
    lines = FakeWriter.instances[0].batches[0]
    assert ",tracker_name=Bus " in lines[0]
    assert ",tracker_id=8,tracker_name=tracker\\ 8 " in lines[1]
    assert ",tracker_name=Bus " in lines[2]


def test_ingest_rows_of_a_tracker_gone_from_the_account_do_not_block_the_others(env, monkeypatch):
    _cache_rows(_row(1), _row(2, tracker=8), _row(3))
    monkeypatch.setattr("trackiwi.cli.Client", _stub_client(trackers=[{"id": 7, "name": "Bus"}]))
    assert cli.main(["ingest"]) == 0
    assert len(FakeWriter.instances[0].batches[0]) == 3
    with Store() as store:
        assert store.tracker_names() == {7: "Bus", 8: "tracker 8"}
        assert store.mirror_position("k") == 3


def test_ingest_replaces_a_fallback_with_the_real_name_once_there_is_one(env, monkeypatch):
    _cache_rows(_row(1, tracker=8), names={8: "tracker 8"})
    monkeypatch.setattr("trackiwi.cli.Client", _stub_client(trackers=[{"id": 8, "name": "Van"}]))
    assert cli.main(["ingest"]) == 0
    assert ",tracker_name=Van " in FakeWriter.instances[0].batches[0][0]


def test_ingest_never_replaces_a_real_name_with_the_fallback(env, monkeypatch):
    _cache_rows(_row(1, tracker=8), names={8: "Van"})
    monkeypatch.setattr("trackiwi.cli.Client", _stub_client(trackers=[{"id": 8, "name": ""}]))
    assert cli.main(["ingest"]) == 0
    assert ",tracker_name=Van " in FakeWriter.instances[0].batches[0][0]
    with Store() as store:
        assert store.tracker_names() == {8: "Van"}


# --- ingest reports each failure separately (final review M-ingest) ---------


def test_ingest_reports_a_name_refresh_failure_as_such(env, monkeypatch, capsys):
    _cache_rows(_row(1))
    monkeypatch.setattr(
        "trackiwi.cli.Client",
        _stub_client(trackers_error=TrackiwiError("network error: down")),
    )
    assert cli.main(["ingest"]) == 1
    err = capsys.readouterr().err
    assert "tracker names not refreshed: network error: down" in err
    assert "sync failed" not in err
    # The names already stored from an earlier run still let the push go out.
    assert len(FakeWriter.instances[0].batches[0]) == 1


def test_ingest_refreshes_names_even_when_sync_fails(env, monkeypatch):
    monkeypatch.setattr(
        "trackiwi.cli.Client",
        _stub_client(
            trackers=[{"id": 7, "name": "Bus"}], sync_error=TrackiwiError("network error: down")
        ),
    )
    with Store() as store:
        store.upsert([_row(1)])
    assert cli.main(["ingest"]) == 1
    assert ",tracker_name=Bus " in FakeWriter.instances[0].batches[0][0]


def test_ingest_sync_and_push_both_failing_keeps_the_sync_exit_code(env, monkeypatch, capsys):
    _cache_rows(_row(1))

    def refuse(self, lines):
        raise TrackiwiError("InfluxDB write failed (HTTP 500): boom")

    monkeypatch.setattr(FakeWriter, "write", refuse)
    monkeypatch.setattr("trackiwi.cli.Client", _stub_client(authenticated=False))
    assert cli.main(["ingest"]) == 2
    err = capsys.readouterr().err
    assert "push failed: InfluxDB write failed (HTTP 500): boom" in err
    assert "authentication required" in err


# --- A sync timeout still lets the push run (final review I2) ---------------


def test_ingest_sync_timeout_still_pushes(env, monkeypatch, capsys):
    """The real `Client` transport times out; the push must still run."""
    _cache_rows(_row(1))

    class TimingOut(Client):
        @classmethod
        def load(cls, *args, **kwargs):
            return Client(
                api_base="https://api.example.invalid",
                token="tok",
                user_id=42,
                opener=FakeOpener(TimeoutError("timed out"), TimeoutError("timed out")),
            )

    monkeypatch.setattr("trackiwi.cli.Client", TimingOut)
    assert cli.main(["ingest"]) == 1
    assert len(FakeWriter.instances[0].batches[0]) == 1
    err = capsys.readouterr().err
    assert "sync failed" in err
    assert "timed out" in err
