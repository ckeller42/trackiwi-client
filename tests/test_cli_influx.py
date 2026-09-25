"""CLI wiring for InfluxDB: check, push, and ingest (push runs even if sync fails)."""

import pytest

from trackiwi import AuthError, TrackiwiError, cli
from trackiwi.store import Store


def _row(pid):
    return (pid, 7, 1700000000 + pid, 1, 31.0, -41.0, 12, 0.0, 90, 150, 5, 9, 99, 1287)


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


def _stub_client(batches=None, trackers=None, sync_error=None, authenticated=True):
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
    with Store() as store:
        store.upsert([_row(1), _row(2)])
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
    with Store() as store:
        store.upsert([_row(1)])
    monkeypatch.setattr(
        "trackiwi.cli.Client", _stub_client(sync_error=TrackiwiError("network error: down"))
    )
    assert cli.main(["ingest"]) == 1
    assert len(FakeWriter.instances[0].batches[0]) == 1
    assert "network error" in capsys.readouterr().err


def test_ingest_auth_failure_still_pushes_and_exits_two(env, monkeypatch):
    with Store() as store:
        store.upsert([_row(1)])
    monkeypatch.setattr("trackiwi.cli.Client", _stub_client(authenticated=False))
    assert cli.main(["ingest"]) == 2
    assert len(FakeWriter.instances[0].batches[0]) == 1


def test_sync_command_still_works(env, monkeypatch, capsys):
    monkeypatch.setattr("trackiwi.cli.Client", _stub_client(batches=[([_row(1)], 0, 1)]))
    assert cli.main(["sync"]) == 0
    assert "1 new positions" in capsys.readouterr().out


def test_auth_error_type_is_preserved():
    assert issubclass(AuthError, TrackiwiError)
