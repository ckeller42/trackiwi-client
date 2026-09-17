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
