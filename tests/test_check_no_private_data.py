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


# --- A trackiwi export under any filename is caught, not only track suffixes (#27) ---
# The GPX marker is built by concatenation so this test file does not itself
# carry the contiguous fingerprint the guard hunts for.
_GPX_MARKER = 'creator="' + "trackiwi-client/0.2.0"


def test_a_csv_export_under_a_plain_txt_name_is_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "positions.txt", "id,tracker_id,fix_at,latitude\n1,7,1700000000,31.0\n")
    assert check_paths([rel]) != []


def test_a_gpx_export_with_no_suffix_is_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "track", f'<gpx {_GPX_MARKER}"><trk/></gpx>\n')
    assert check_paths([rel]) != []


def test_a_geojson_export_saved_as_jsonl_is_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "track.jsonl", FEATURE_COLLECTION)
    assert check_paths([rel]) != []


def test_a_synthetic_csv_fixture_under_any_name_is_allowed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(
        tmp_path, "tests/fixtures/synthetic-sample.txt", "id,tracker_id,fix_at,x\n1,7,1,2\n"
    )
    assert check_paths([rel]) == []


def test_source_with_the_export_field_names_is_not_flagged(tmp_path, monkeypatch):
    """export.py and tests mention tracker_id/point_count; they are not exports."""
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "trackiwi/export.py", 'd = {"tracker_id": 7, "point_count": 3}\n')
    assert check_paths([rel]) == []


def test_credential_file_is_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "config.json", "{}\n")
    assert check_paths([rel]) != []


# --- InfluxDB secrets: influx.toml and the token/password files it points at ---
#
# `influx.toml` names the target and the *paths* of its credential files
# (`token_file`, `password_file`); the natural names for those files are
# `influx.token` / `influx.password`, or the bare dotfiles `.token` /
# `.password`. None of these used to be caught by name, only by the secret
# scan — which a short token, or a `.toml` that merely points at the file,
# slips past. The committed templates are `influx.example.toml` and
# `example.env`, which no rule here matches.


@pytest.mark.parametrize(
    "rel",
    [
        "influx.toml",
        "deploy/influx.toml",
        "influx.token",
        "secrets/grafana.token",
        ".token",
        "influx.password",
        ".password",
        "deploy/.password",
    ],
)
def test_influx_credential_files_are_blocked(tmp_path, monkeypatch, rel):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, rel, "short\n")
    assert any("credential" in p for p in check_paths([rel]))


@pytest.mark.parametrize("rel", ["examples/influx.example.toml", "deploy/example.env"])
def test_influx_templates_stay_committable(tmp_path, monkeypatch, rel):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, rel, 'url = "http://localhost:8086"\n')
    assert check_paths([rel]) == []
