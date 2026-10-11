"""InfluxDB target configuration: file < environment, token sources, 0600."""

import os
import stat

import pytest

from trackiwi import TrackiwiError
from trackiwi.influx import default_influx_config_path, load_config


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_default_path_honours_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_influx_config_path() == tmp_path / "trackiwi" / "influx.toml"


def test_file_values_load(tmp_path):
    path = _write(
        tmp_path / "influx.toml",
        'url = "http://localhost:8086"\nversion = 2\norg = "home"\nbucket = "trackiwi"\n',
    )
    cfg = load_config(path, env={"TRACKIWI_INFLUX_TOKEN": "tok"})
    assert cfg.url == "http://localhost:8086"
    assert cfg.version == 2
    assert (cfg.org, cfg.bucket, cfg.token) == ("home", "trackiwi", "tok")


def test_environment_overrides_file(tmp_path):
    path = _write(tmp_path / "influx.toml", 'url = "http://file:8086"\nbucket = "a"\n')
    cfg = load_config(
        path, env={"TRACKIWI_INFLUX_URL": "http://env:8086", "TRACKIWI_INFLUX_BUCKET": "b"}
    )
    assert cfg.url == "http://env:8086"
    assert cfg.bucket == "b"


def test_environment_alone_is_enough(tmp_path):
    cfg = load_config(tmp_path / "missing.toml", env={"TRACKIWI_INFLUX_URL": "http://h:8086"})
    assert cfg.url == "http://h:8086"
    assert cfg.version is None


def test_trailing_slash_is_removed_from_url(tmp_path):
    cfg = load_config(tmp_path / "missing.toml", env={"TRACKIWI_INFLUX_URL": "http://h:8086/"})
    assert cfg.url == "http://h:8086"


def test_version_values(tmp_path):
    for raw, expected in (("auto", None), ("1", 1), ("2", 2)):
        env = {"TRACKIWI_INFLUX_URL": "http://h", "TRACKIWI_INFLUX_VERSION": raw}
        assert load_config(tmp_path / "missing.toml", env=env).version == expected
    with pytest.raises(TrackiwiError, match="version"):
        load_config(
            tmp_path / "missing.toml",
            env={"TRACKIWI_INFLUX_URL": "http://h", "TRACKIWI_INFLUX_VERSION": "3"},
        )


def test_missing_url_names_the_key_and_file(tmp_path):
    with pytest.raises(TrackiwiError, match="url.*TRACKIWI_INFLUX_URL"):
        load_config(tmp_path / "missing.toml", env={})


def test_http_to_a_remote_host_warns_but_local_and_https_do_not(tmp_path, capsys):
    # http to a non-local host leaks the token and history in the clear (#26).
    load_config(
        tmp_path / "missing.toml", env={"TRACKIWI_INFLUX_URL": "http://influx.example:8086"}
    )
    assert "unencrypted" in capsys.readouterr().err
    for safe in ("http://localhost:8086", "http://127.0.0.1:8086", "https://influx.example:8086"):
        load_config(tmp_path / "missing.toml", env={"TRACKIWI_INFLUX_URL": safe})
        assert capsys.readouterr().err == ""


def test_a_url_without_a_scheme_is_rejected(tmp_path):
    # Without this, Request() raises a raw ValueError outside the try (#19).
    for bad in ("influx.local", "influx.local:8086", "ftp://h", "://h"):
        with pytest.raises(TrackiwiError, match="http"):
            load_config(tmp_path / "missing.toml", env={"TRACKIWI_INFLUX_URL": bad})


def test_unknown_key_is_rejected(tmp_path):
    path = _write(tmp_path / "influx.toml", 'url = "http://h"\ntoken = "plaintext"\n')
    with pytest.raises(TrackiwiError, match="unknown key 'token'"):
        load_config(path, env={})


def test_invalid_toml_is_a_clear_error(tmp_path):
    path = _write(tmp_path / "influx.toml", "url = \n")
    with pytest.raises(TrackiwiError, match="not valid TOML"):
        load_config(path, env={})


def test_token_env_names_the_variable_holding_the_token(tmp_path):
    path = _write(tmp_path / "influx.toml", 'url = "http://h"\ntoken_env = "INFLUXDB_TOKEN"\n')
    cfg = load_config(path, env={"INFLUXDB_TOKEN": "from-other-var"})
    assert cfg.token == "from-other-var"
    assert cfg.token_env == "INFLUXDB_TOKEN"


def test_token_file_is_read_and_trailing_newline_stripped(tmp_path):
    token_file = _write(tmp_path / "influx.token", "tok-from-file\n")
    path = _write(tmp_path / "influx.toml", f'url = "http://h"\ntoken_file = "{token_file}"\n')
    assert load_config(path, env={}).token == "tok-from-file"


def test_token_file_is_self_healed_to_0600(tmp_path):
    # A token_file (or a .env from the template) is otherwise left world-readable (#40).
    token_file = _write(tmp_path / "influx.token", "tok-from-file\n")
    os.chmod(token_file, 0o644)
    path = _write(tmp_path / "influx.toml", f'url = "http://h"\ntoken_file = "{token_file}"\n')
    load_config(path, env={})
    assert stat.S_IMODE(os.stat(token_file).st_mode) == 0o600


def test_token_env_wins_over_token_file(tmp_path):
    token_file = _write(tmp_path / "influx.token", "from-file")
    path = _write(tmp_path / "influx.toml", f'url = "http://h"\ntoken_file = "{token_file}"\n')
    assert load_config(path, env={"TRACKIWI_INFLUX_TOKEN": "from-env"}).token == "from-env"


def test_missing_token_file_names_the_file(tmp_path):
    path = _write(
        tmp_path / "influx.toml", 'url = "http://h"\ntoken_file = "/nonexistent/influx.token"\n'
    )
    with pytest.raises(TrackiwiError, match="/nonexistent/influx.token"):
        load_config(path, env={})


def test_password_file_is_read(tmp_path):
    pw = _write(tmp_path / "influx.password", "s3cret\n")
    path = _write(
        tmp_path / "influx.toml",
        f'url = "http://h"\nversion = 1\ndatabase = "d"\npassword_file = "{pw}"\n',
    )
    assert load_config(path, env={}).password == "s3cret"  # pragma: allowlist secret


def test_load_narrows_a_widened_config_file(tmp_path):
    path = _write(tmp_path / "influx.toml", 'url = "http://h"\n')
    os.chmod(path, 0o644)
    load_config(path, env={})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
