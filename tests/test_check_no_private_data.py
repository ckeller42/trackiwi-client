import sys
from pathlib import Path

import pytest

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


# --- A GeoJSON export named `.json` must not slip past the extension rules ---
#
# `trackiwi export --format geojson -o positions.json` is the natural filename
# (a great deal of GIS and JS tooling expects `.json`), and `.json` was in
# neither TRACK_SUFFIXES nor .gitignore. Because it *is* in TEXT_SUFFIXES the
# file reached the secret scan, which knows nothing about coordinates — so a
# real track passed all three layers. A GeoJSON file identifies itself exactly
# via its own `type` discriminator, the same class of check as the SQLite
# header sniff.

FEATURE_COLLECTION = (
    '{"type": "FeatureCollection", "features": '
    '[{"type": "Feature", "geometry": {"type": "LineString", '
    '"coordinates": [[9.0, 48.0], [9.001, 48.001]]}, "properties": {}}]}\n'
)


def test_geojson_saved_as_json_is_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "positions.json", FEATURE_COLLECTION)
    assert check_paths([rel]) != []


@pytest.mark.parametrize(
    "geojson_type",
    ["Feature", "GeometryCollection", "Point", "LineString", "Polygon", "MultiLineString"],
)
def test_every_geojson_discriminator_is_blocked(tmp_path, monkeypatch, geojson_type):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "export.json", '{"type": "' + geojson_type + '"}\n')
    assert check_paths([rel]) != []


def test_synthetic_json_fixture_is_allowed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "tests/fixtures/synthetic-track.json", FEATURE_COLLECTION)
    assert check_paths([rel]) == []


def test_synthetic_named_subdirectory_does_not_launder_real_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "tests/fixtures/synthetic-dump/real.json", FEATURE_COLLECTION)
    assert check_paths([rel]) != []


def test_unparseable_json_is_not_treated_as_a_track(tmp_path, monkeypatch):
    """A malformed config, a fixture or a lockfile is not a GeoJSON export.
    The guard must not error out on it either — it falls through to the
    existing secret scan."""
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "broken.json", "{ this is not json\n")
    assert check_paths([rel]) == []


def test_an_ordinary_json_file_still_passes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "tsconfig.json", '{"compilerOptions": {"strict": true}}\n')
    assert check_paths([rel]) == []


def test_json_list_is_not_treated_as_a_track(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "data.json", "[1, 2, 3]\n")
    assert check_paths([rel]) == []


def test_gpx_saved_as_xml_is_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "track.xml", "<gpx/>\n")
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
