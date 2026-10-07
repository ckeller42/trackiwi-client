"""CLI `trackers` command. All fixtures synthetic."""

import pytest

from trackiwi.cli import main


def stub_client(records):
    class StubClient:
        @classmethod
        def load(cls):
            return cls()

        def trackers(self):
            return records

    return StubClient


def test_trackers_prints_one_tab_separated_line_per_record(capsys, monkeypatch):
    monkeypatch.setattr("trackiwi.cli.Client", stub_client([{"id": 7, "name": "Van"}, {"id": 8}]))
    assert main(["trackers"]) == 0
    assert capsys.readouterr().out == "7\tVan\n8\t(unnamed)\n"


def test_trackers_keeps_a_tab_or_newline_in_a_name_out_of_the_columns(capsys, monkeypatch):
    monkeypatch.setattr("trackiwi.cli.Client", stub_client([{"id": 7, "name": "a\tb\nc"}]))
    assert main(["trackers"]) == 0
    assert capsys.readouterr().out == "7\ta b c\n"


def test_trackers_survives_a_non_dict_element(capsys, monkeypatch):
    monkeypatch.setattr("trackiwi.cli.Client", stub_client(["oops", {}]))
    assert main(["trackers"]) == 0
    assert capsys.readouterr().out == "oops\n-\t(unnamed)\n"


def test_trackers_never_touches_the_local_cache(monkeypatch):
    from trackiwi.store import default_db_path

    monkeypatch.setattr("trackiwi.cli.Client", stub_client([{"id": 7}]))
    assert main(["trackers"]) == 0
    assert not default_db_path().exists()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path
