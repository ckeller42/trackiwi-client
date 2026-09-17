import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from check_no_private_data import check_paths  # noqa: E402


def _write(tmp_path, rel, text="hello\n"):
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return rel


def test_clean_files_pass(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "trackiwi/client.py", "x = 1\n")
    assert check_paths([rel]) == []


def test_database_is_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "positions.db")
    assert any("database" in p for p in check_paths([rel]))


def test_track_export_outside_fixtures_is_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "route.gpx", "<gpx/>\n")
    assert check_paths([rel]) != []


def test_synthetic_fixture_is_allowed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "tests/fixtures/synthetic-positions.csv", "1,2\n")
    assert check_paths([rel]) == []


def test_synthetic_named_subdirectory_does_not_launder_real_tracks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(
        tmp_path,
        "tests/fixtures/synthetic-dump/2026-09-17-real-track.gpx",
        "<gpx/>\n",
    )
    assert check_paths([rel]) != []


def test_sqlite_header_is_blocked_regardless_of_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = "notes.bak"
    (tmp_path / rel).write_bytes(b"SQLite format 3\x00" + b"\x00" * 16)
    assert any("database" in p for p in check_paths([rel]))


def test_credential_file_is_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "config.json", "{}\n")
    assert check_paths([rel]) != []


def test_token_shaped_string_is_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    secret = "a" * 44
    rel = _write(tmp_path, "notes.md", f"token = {secret}\n")
    assert any("token-shaped" in p for p in check_paths([rel]))


def test_allow_secret_marker_suppresses(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    secret = "a" * 44
    rel = _write(tmp_path, "notes.md", f"token = {secret}  # allow-secret\n")
    assert check_paths([rel]) == []
