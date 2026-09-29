import json

import pytest

from trackiwi.cli import main
from trackiwi.store import Store


def row(pos_id=1, fix_at=1758000000, tracker_id=7, lat=31.0, lon=-41.0):
    return (pos_id, tracker_id, fix_at, 120, lat, lon, 12, 0.0, 0, 0, -71, 9, 98, 4120)


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


def test_export_without_tracker_filter_keeps_the_devices_apart(capsys):
    """The cross-module seam: `cmd_export` hands the exporters whatever
    `store.query()` returns, which for the documented default (no `--tracker`)
    is every tracker's rows interleaved by time."""
    import xml.etree.ElementTree as ET

    with Store() as store:
        store.upsert(
            [
                row(1, fix_at=1758000000, tracker_id=101, lat=48.0, lon=9.0),
                row(2, fix_at=1758000010, tracker_id=202, lat=20.0, lon=-80.0),
                row(3, fix_at=1758000020, tracker_id=101, lat=48.001, lon=9.0),
                row(4, fix_at=1758000030, tracker_id=202, lat=20.001, lon=-80.0),
            ]
        )
    assert main(["export", "--format", "gpx"]) == 0
    tree = ET.fromstring(capsys.readouterr().out)
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    assert len(tree.findall("g:trk", ns)) == 2


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


def test_export_to_existing_file_preserves_mode(tmp_path):
    import os
    import stat

    seed_cache()
    target = tmp_path / "out.csv"
    target.write_text("previous export\n")
    # Make it world-readable (0644).
    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
    mode_before = stat.S_IMODE(os.stat(target).st_mode)
    assert mode_before == 0o644

    assert main(["export", "--format", "csv", "-o", str(target)]) == 0

    mode_after = stat.S_IMODE(os.stat(target).st_mode)
    assert mode_after == 0o644


def test_export_to_new_file_creates_mode_0600(tmp_path):
    import os
    import stat

    seed_cache()
    target = tmp_path / "out.csv"
    assert not target.exists()

    assert main(["export", "--format", "csv", "-o", str(target)]) == 0

    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode == 0o600


def test_export_to_symlink_follows_and_preserves_symlink(tmp_path):
    import os

    seed_cache()
    real_file = tmp_path / "real.csv"
    real_file.write_text("original content\n")
    symlink = tmp_path / "link.csv"
    os.symlink(real_file, symlink)

    assert main(["export", "--format", "csv", "-o", str(symlink)]) == 0

    # Verify the symlink still exists and still points to real_file.
    assert symlink.is_symlink()
    assert os.readlink(symlink) == str(real_file)
    # Verify the real file was updated.
    assert real_file.read_text() != "original content\n"


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


def test_sync_with_only_malformed_rows_reports_them_and_succeeds(capsys, monkeypatch):
    """Issue #18: a page may carry no usable row at all and still be progress."""
    seed_cache()
    monkeypatch.setattr("trackiwi.cli.Client", make_stub_sync_client([([], 1, None)]))

    assert main(["sync"]) == 0

    captured = capsys.readouterr()
    assert "skipped 1 malformed rows" in captured.err
    assert "0 new positions, 2 cached in total" in captured.out


def test_sync_does_not_report_refetched_rows_as_new(capsys, monkeypatch):
    seed_cache()
    batch = ([row(1), row(2, fix_at=1758000060)], 0, 2)
    monkeypatch.setattr("trackiwi.cli.Client", make_stub_sync_client([batch]))

    assert main(["sync", "--full"]) == 0

    out, err = capsys.readouterr()
    assert "0 new positions, 2 cached in total" in out
    # The progress line still reports what was fetched, which is what the
    # server's total header is comparable with.
    assert "synced 2 of 2 positions" in err


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


# --- Fix 8: `--token -` keeps the token out of argv and shell history ---


def test_login_reads_the_token_from_stdin(monkeypatch):
    """A token passed as `--token <value>` is written to the shell history file
    and is visible in `ps -ww` to every process running as the same user.
    `--token -` reads it from stdin, which touches neither."""
    import io

    from trackiwi.client import default_config_path

    monkeypatch.setattr("sys.stdin", io.StringIO("tok-from-stdin\n"))
    assert main(["login", "--token", "-", "--api-base", "https://api.example.invalid"]) == 0
    saved = json.loads(default_config_path().read_text())
    assert saved["token"] == "tok-from-stdin"


def test_login_with_empty_stdin_token_reports_a_clean_error(monkeypatch, capsys):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["login", "--token", "-", "--api-base", "https://api.example.invalid"]) == 1
    assert "token" in capsys.readouterr().err


def test_login_prompts_without_echo_when_stdin_is_a_tty(monkeypatch):
    from trackiwi.client import default_config_path

    class Tty:
        def isatty(self):
            return True

        def read(self):  # pragma: no cover - must not be reached on a tty
            raise AssertionError("a tty must be prompted, not read")

    monkeypatch.setattr("sys.stdin", Tty())
    monkeypatch.setattr("trackiwi.cli.getpass.getpass", lambda prompt="": "tok-typed")
    assert main(["login", "--token", "-", "--api-base", "https://api.example.invalid"]) == 0
    assert json.loads(default_config_path().read_text())["token"] == "tok-typed"


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


# --- Fix 7: no exception class escapes main() as a raw traceback ---


def _corrupt_cache():
    from trackiwi.store import default_db_path

    path = default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a database, just some bytes\n" * 8)
    return path


def _corrupt_config():
    from trackiwi.client import default_config_path

    path = default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"api_base": "https://api.example.invalid", "tok')
    return path


def test_export_with_a_corrupt_cache_reports_a_clean_error(capsys):
    _corrupt_cache()
    assert main(["export", "--format", "csv"]) == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "purge" in err
    assert "Traceback" not in err


def test_purge_deletes_a_corrupt_cache(capsys):
    """Spec section 7.1: `purge` must actually delete the movement history.
    It used to open the database first, so on a corrupt file it raised before
    reaching the unlink — failing in exactly the case where a user most wants
    the file gone."""
    path = _corrupt_cache()
    assert main(["purge", "--yes"]) == 0
    assert not path.exists()


def test_purge_does_not_create_a_cache_that_does_not_exist(capsys):
    from trackiwi.store import default_db_path

    assert main(["purge", "--yes"]) == 0
    assert not default_db_path().exists()
    assert not default_db_path().parent.exists()


def test_export_does_not_create_a_cache_that_does_not_exist(capsys):
    from trackiwi.store import default_db_path

    assert main(["export", "--format", "csv"]) == 0
    assert not default_db_path().exists()


def test_a_corrupt_config_reports_a_clean_error(capsys):
    _corrupt_config()
    assert main(["trackers"]) == 1
    err = capsys.readouterr().err
    assert "config file is corrupt" in err
    assert "Traceback" not in err


def test_export_of_an_unrepresentable_timestamp_reports_a_clean_error(capsys):
    """A `fix_at` already in the cache from before the parse-time range check
    must not crash the exporter with an uncaught ValueError."""
    with Store() as store:
        store.upsert([row(1, fix_at=10_000_000_001_000_000)])
    assert main(["export", "--format", "gpx"]) == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "Traceback" not in err


def test_export_survives_a_closed_pipe(monkeypatch, capsys):
    """`trackiwi export --format csv | head` closes the pipe early; that used
    to surface as an uncaught BrokenPipeError."""
    seed_cache()

    def broken(*_args, **_kwargs):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr("sys.stdout.write", broken)
    assert main(["export", "--format", "csv"]) == 0


def test_login_without_email_on_closed_stdin_reports_a_clean_error(monkeypatch, capsys):
    def eof(*_args, **_kwargs):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert main(["login"]) == 1
    err = capsys.readouterr().err
    assert "--email" in err
    assert "Traceback" not in err


def test_logout_on_success_reports_revocation(capsys, monkeypatch):
    calls = []

    class StubClient:
        @classmethod
        def load(cls):
            return cls()

        def logout(self):
            calls.append(True)
            return True

    monkeypatch.setattr("trackiwi.cli.Client", StubClient)

    assert main(["logout"]) == 0
    assert calls == [True]
    assert "revoked" in capsys.readouterr().out.lower()


def test_logout_on_revocation_failure_warns_to_stderr(capsys, monkeypatch):
    calls = []

    class StubClient:
        @classmethod
        def load(cls):
            return cls()

        def logout(self):
            calls.append(True)
            return False  # Revocation failed but local state was cleared

    monkeypatch.setattr("trackiwi.cli.Client", StubClient)

    assert main(["logout"]) == 0
    assert calls == [True]
    err = capsys.readouterr().err
    assert "could not revoke" in err.lower()
    assert "may still be valid" in err.lower()
    assert "trackiwi app" in err.lower()


# --- N-1: the advice on a locked cache must not be "delete it" ---


def test_export_with_a_locked_cache_does_not_advise_purge(capsys, monkeypatch):
    """A concurrent `trackiwi` holding a write lock was reported as a corrupt
    cache, and the advertised remedy (`purge --yes`) deletes the intact
    history."""
    import sqlite3

    from trackiwi.store import default_db_path

    seed_cache()
    real_connect = sqlite3.connect
    monkeypatch.setattr(
        sqlite3, "connect", lambda db, *a, **k: real_connect(db, *a, **{**k, "timeout": 0.05})
    )
    holder = sqlite3.connect(default_db_path())
    holder.execute("BEGIN EXCLUSIVE")
    try:
        assert main(["export", "--format", "csv"]) == 1
    finally:
        holder.rollback()
        holder.close()
    err = capsys.readouterr().err
    assert "purge" not in err
    assert "locked" in err
    assert "Traceback" not in err
    with Store() as store:
        assert store.count() == 2


# --- N-2: a bad config `api_base` must be catchable and recoverable ---


def _write_raw_config(text):
    from trackiwi.client import default_config_path

    path = default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_non_string_api_base_reports_a_clean_error(capsys):
    _write_raw_config('{"api_base": 123, "token": "tok", "user_id": 1}')
    assert main(["trackers"]) == 1
    err = capsys.readouterr().err
    assert "config file is corrupt" in err
    assert "Traceback" not in err


def test_logout_with_an_http_api_base_still_removes_the_credentials(capsys):
    """An `http://` base in the config made `Client.load()` raise before
    `logout` could run, so the stored token — which grants live vehicle
    location — could never be revoked or even deleted with the tool."""
    path = _write_raw_config(
        '{"api_base": "http://api.example.invalid", "token": "live-token", "user_id": 1}'
    )
    assert main(["logout"]) == 0
    err = capsys.readouterr().err
    assert not path.exists()
    assert "may still be valid" in err.lower()
    assert "trackiwi app" in err.lower()
    assert "live-token" not in err
    assert "Traceback" not in err


# --- N-3: a partial sync must not report success ---


def test_sync_with_a_dead_stderr_does_not_report_success(monkeypatch):
    """`trackiwi sync 2>&1 | head -1` kills the writer of the progress line.
    `main()`'s blanket `except BrokenPipeError: return 0` then reported a sync
    that stopped after its first page as a success, so a wrapper running
    `trackiwi sync && trackiwi export` proceeded on incomplete data."""
    import io

    batches = [([row(1)], 0, 3), ([row(2, fix_at=1758000060)], 0, 3)]
    monkeypatch.setattr("trackiwi.cli.Client", make_stub_sync_client(batches))

    class DeadStderr(io.StringIO):
        def write(self, text):
            raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr("sys.stderr", DeadStderr())
    assert main(["sync"]) != 0


# --- N-4: GPX must not emit a non-finite coordinate ---


def test_export_of_a_cached_non_finite_coordinate_reports_a_clean_error(capsys):
    """`cmd_export`'s comment claims the boundary conversion catches a
    non-finite coordinate already in the cache. It did so only for GeoJSON."""
    with Store() as store:
        store.upsert([row(1, lat=float("inf"))])
    assert main(["export", "--format", "gpx"]) == 1
    out, err = capsys.readouterr()
    assert 'lat="inf"' not in out
    assert "error:" in err
    assert "Traceback" not in err
