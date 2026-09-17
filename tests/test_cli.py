import json

import pytest

from trackiwi.cli import main
from trackiwi.store import Store


def row(pos_id=1, fix_at=1758000000):
    return (pos_id, 7, fix_at, 120, 31.0, -41.0, 12, 0.0, 0, 0, -71, 9, 98, 4120)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path


def seed_cache():
    with Store() as store:
        store.upsert([row(1), row(2, fix_at=1758000060)])


def test_export_writes_gpx_to_stdout(capsys):
    seed_cache()
    assert main(["export", "--format", "gpx"]) == 0
    assert "<trkpt" in capsys.readouterr().out


def test_export_writes_to_a_file(tmp_path):
    seed_cache()
    target = tmp_path / "out.geojson"
    assert main(["export", "--format", "geojson", "-o", str(target)]) == 0
    assert json.loads(target.read_text())["features"][0]["geometry"]["type"] == "LineString"


def test_export_filters_by_date(capsys):
    seed_cache()
    assert main(["export", "--format", "csv", "--from", "2000-01-01", "--to", "2000-01-02"]) == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 1  # header only


def test_export_rejects_a_bad_date(capsys):
    seed_cache()
    assert main(["export", "--format", "csv", "--from", "not-a-date"]) == 1
    assert "date" in capsys.readouterr().err.lower()


def test_commands_requiring_auth_exit_2(capsys):
    assert main(["trackers"]) == 2
    assert "login" in capsys.readouterr().err


def test_purge_deletes_the_cache(capsys):
    seed_cache()
    from trackiwi.store import default_db_path

    assert default_db_path().exists()
    assert main(["purge", "--yes"]) == 0
    assert not default_db_path().exists()


def test_purge_without_confirmation_refuses(capsys):
    seed_cache()
    assert main(["purge"]) == 1
    assert "--yes" in capsys.readouterr().err


# --- Fix 1: `export -o` must not leak tracebacks or clobber a good file ---


def test_export_to_a_directory_reports_a_clean_error(tmp_path, capsys):
    seed_cache()
    target_dir = tmp_path / "somedir"
    target_dir.mkdir()
    assert main(["export", "--format", "csv", "-o", str(target_dir)]) == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "Traceback" not in err


def test_export_write_failure_does_not_clobber_a_previous_export(tmp_path, monkeypatch):
    seed_cache()
    target = tmp_path / "out.csv"
    target.write_text("previous export\n")

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("trackiwi.cli.os.replace", boom)

    assert main(["export", "--format", "csv", "-o", str(target)]) == 1
    assert target.read_text() == "previous export\n"
    assert list(tmp_path.glob(".out.csv.*.tmp")) == []


# --- Fix 2: `login --token` requires `--api-base`, and vice versa ---


@pytest.mark.parametrize(
    "args",
    [
        ["login", "--token", "abc"],
        ["login", "--api-base", "https://example.com"],
    ],
)
def test_login_requires_token_and_api_base_together(args, capsys):
    assert main(args) == 1
    assert "--token and --api-base" in capsys.readouterr().err


# --- Fix 3: an inverted date range is an error, not a silent empty export ---


def test_export_rejects_an_inverted_date_range(capsys):
    seed_cache()
    assert main(["export", "--format", "csv", "--from", "2026-01-01", "--to", "2020-01-01"]) == 1
    assert "after" in capsys.readouterr().err.lower()


# --- Fix 4: cover cmd_sync, cmd_login and cmd_logout with a stubbed Client ---


def make_stub_sync_client(batches, authenticated=True):
    """A stand-in for `Client` that plays back canned `sync()` batches."""

    class StubClient:
        def __init__(self):
            self.authenticated = authenticated

        @classmethod
        def load(cls):
            return cls()

        def sync(self, offset=None):
            for batch in batches:
                if isinstance(batch, BaseException):
                    raise batch
                yield batch

    return StubClient


def test_sync_writes_batches_and_reports_counts(capsys, monkeypatch):
    batch = ([row(1), row(2, fix_at=1758000060)], 0, 2)
    monkeypatch.setattr("trackiwi.cli.Client", make_stub_sync_client([batch]))

    assert main(["sync"]) == 0

    with Store() as store:
        assert store.count() == 2
    assert "2 new positions, 2 cached in total" in capsys.readouterr().out


def test_sync_persists_batches_before_a_later_failure(monkeypatch):
    """The resume guarantee: a batch already yielded stays durable even if a
    later page raises, so `store.max_id()` reflects it and re-running `sync`
    resumes from there instead of losing work."""
    from trackiwi.client import TrackiwiError as ClientTrackiwiError

    first_batch = ([row(1)], 0, None)
    monkeypatch.setattr(
        "trackiwi.cli.Client",
        make_stub_sync_client([first_batch, ClientTrackiwiError("boom mid-sync")]),
    )

    assert main(["sync"]) == 1

    with Store() as store:
        assert store.max_id() == 1


def test_sync_failure_still_terminates_the_progress_line(monkeypatch, capsys):
    """Fix 5: the \\r progress line must end with a real newline before any
    error text, on success or failure, so the two never run together."""
    from trackiwi.client import TrackiwiError as ClientTrackiwiError

    monkeypatch.setattr(
        "trackiwi.cli.Client",
        make_stub_sync_client([([row(1)], 0, None), ClientTrackiwiError("boom mid-sync")]),
    )

    assert main(["sync"]) == 1
    err = capsys.readouterr().err
    assert "\rsynced 1 positions\nerror: boom mid-sync" in err


def test_sync_does_not_create_cache_when_unauthenticated(capsys):
    """Fix 6: authentication is checked before Store() is ever opened."""
    from trackiwi.store import default_db_path

    assert main(["sync"]) == 2
    assert "login" in capsys.readouterr().err
    assert not default_db_path().exists()


def test_login_never_persists_the_password(monkeypatch):
    captured = {}

    class StubClient:
        def __init__(self):
            pass

        def login(self, email, password):
            captured["email"] = email
            captured["password"] = password
            from trackiwi.client import default_config_path

            path = default_config_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"api_base": "https://example.com", "token": "tok", "user_id": 1})
            )
            return {"id": 1}

    monkeypatch.setattr("trackiwi.cli.Client", StubClient)
    monkeypatch.setattr("trackiwi.cli.getpass.getpass", lambda prompt="": "hunter2")

    assert main(["login", "--email", "user@example.com"]) == 0
    assert captured == {"email": "user@example.com", "password": "hunter2"}

    from trackiwi.client import default_config_path

    saved = json.loads(default_config_path().read_text())
    assert "password" not in saved


def test_logout_calls_through_and_returns_0(capsys, monkeypatch):
    calls = []

    class StubClient:
        @classmethod
        def load(cls):
            return cls()

        def logout(self):
            calls.append(True)

    monkeypatch.setattr("trackiwi.cli.Client", StubClient)

    assert main(["logout"]) == 0
    assert calls == [True]
    assert "revoked" in capsys.readouterr().out.lower()
