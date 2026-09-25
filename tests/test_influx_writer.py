"""InfluxDB writer: version detection, exact write requests, errors, redaction."""

import base64
import gzip
import json
import urllib.error
import urllib.parse
from typing import Any

import pytest
from conftest import FakeOpener, FakeResponse

from trackiwi import TrackiwiError
from trackiwi.influx import InfluxConfig, InfluxWriter


def _cfg(**over: Any) -> InfluxConfig:
    base: dict[str, Any] = dict(
        url="http://influx.example.invalid:8086",
        version=2,
        org="home",
        bucket="trackiwi",
        database=None,
        username=None,
        token="tok-secret",  # pragma: allowlist secret
        password=None,
        token_env="TRACKIWI_INFLUX_TOKEN",
    )
    base.update(over)
    return InfluxConfig(**base)


def test_influx_and_client_do_not_import_each_other():
    """Module boundary: influx.py talks to InfluxDB, client.py to trackiwi, never both."""
    import ast
    import pathlib

    import trackiwi

    pkg = pathlib.Path(trackiwi.__file__).parent
    for source, other in (("influx.py", "client"), ("client.py", "influx")):
        tree = ast.parse((pkg / source).read_text(encoding="utf-8"))
        imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert other not in imported and f"trackiwi.{other}" not in imported, source


def test_detects_v2_from_ping_header():
    opener = FakeOpener(FakeResponse(b"", status=204, headers={"X-Influxdb-Version": "v2.7.10"}))
    writer = InfluxWriter(_cfg(version=None), opener=opener)
    assert writer.version == 2
    assert opener.calls[0].full_url == "http://influx.example.invalid:8086/ping"


def test_detects_v1_from_ping_header():
    opener = FakeOpener(FakeResponse(b"", status=204, headers={"x-influxdb-version": "1.8.10"}))
    assert InfluxWriter(_cfg(version=None), opener=opener).version == 1


def test_cloud_ping_header_means_v2():
    opener = FakeOpener(FakeResponse(b"", status=204, headers={"X-Influxdb-Version": "Cloud"}))
    assert InfluxWriter(_cfg(version=None), opener=opener).version == 2


def test_undetectable_version_asks_for_explicit_setting():
    opener = FakeOpener(FakeResponse(b"", status=204, headers={}))
    with pytest.raises(TrackiwiError, match="version = 1 or 2"):
        InfluxWriter(_cfg(version=None), opener=opener).detect_version()


def test_explicit_version_skips_ping():
    opener = FakeOpener()
    assert InfluxWriter(_cfg(version=2), opener=opener).version == 2
    assert opener.calls == []


def test_v2_write_request_is_exact():
    opener = FakeOpener(FakeResponse(b"", status=204))
    InfluxWriter(_cfg(), opener=opener).write(["m,t=1 f=1.0 1", "m,t=1 f=2.0 2"])
    req = opener.calls[0]
    parsed = urllib.parse.urlsplit(req.full_url)
    assert req.method == "POST"
    assert parsed.path == "/api/v2/write"
    assert urllib.parse.parse_qs(parsed.query) == {
        "org": ["home"],
        "bucket": ["trackiwi"],
        "precision": ["s"],
    }
    assert req.get_header("Authorization") == "Token tok-secret"  # pragma: allowlist secret
    assert req.get_header("Content-encoding") == "gzip"
    assert gzip.decompress(req.data).decode() == "m,t=1 f=1.0 1\nm,t=1 f=2.0 2"


def test_v1_write_request_is_exact_with_basic_auth():
    opener = FakeOpener(FakeResponse(b"", status=204))
    cfg = _cfg(
        version=1, database="trackiwi", username="u", password="p", token=None
    )  # pragma: allowlist secret
    InfluxWriter(cfg, opener=opener).write(["m f=1.0 1"])
    req = opener.calls[0]
    parsed = urllib.parse.urlsplit(req.full_url)
    assert parsed.path == "/write"
    assert urllib.parse.parse_qs(parsed.query) == {"db": ["trackiwi"], "precision": ["s"]}
    assert req.get_header("Authorization") == "Basic " + base64.b64encode(b"u:p").decode()


def test_200_and_204_are_both_success():
    for status in (200, 204):
        InfluxWriter(_cfg(), opener=FakeOpener(FakeResponse(b"", status=status))).write(
            ["m f=1.0 1"]
        )


def test_empty_batch_sends_nothing():
    opener = FakeOpener()
    InfluxWriter(_cfg(), opener=opener).write([])
    assert opener.calls == []


def test_v2_without_token_is_a_clear_error():
    with pytest.raises(TrackiwiError, match="TRACKIWI_INFLUX_TOKEN"):
        InfluxWriter(_cfg(token=None), opener=FakeOpener()).write(["m f=1.0 1"])


def test_v2_without_bucket_is_a_clear_error():
    with pytest.raises(TrackiwiError, match="bucket"):
        InfluxWriter(_cfg(bucket=None), opener=FakeOpener()).write(["m f=1.0 1"])


def test_v1_without_database_is_a_clear_error():
    with pytest.raises(TrackiwiError, match="database"):
        InfluxWriter(_cfg(version=1, database=None), opener=FakeOpener()).write(["m f=1.0 1"])


@pytest.mark.parametrize(
    ("status", "pattern"),
    [
        (401, "rejected the token"),
        (403, "rejected the token"),
        (404, "bucket 'trackiwi'"),
        (500, "500"),
    ],
)
def test_error_statuses_map_to_clear_messages(status, pattern):
    opener = FakeOpener(FakeResponse(b"boom", status=status))
    with pytest.raises(TrackiwiError, match=pattern):
        InfluxWriter(_cfg(), opener=opener).write(["m f=1.0 1"])


def test_unreachable_server_is_a_clear_error():
    opener = FakeOpener(urllib.error.URLError("connection refused"))
    with pytest.raises(TrackiwiError, match="cannot reach InfluxDB"):
        InfluxWriter(_cfg(), opener=opener).write(["m f=1.0 1"])


def test_token_and_password_are_redacted_from_error_bodies():
    body = b"echo: Authorization: Token tok-secret and Basic dTpw and pw=hunter2"
    opener = FakeOpener(FakeResponse(body, status=500))
    cfg = _cfg(password="hunter2")  # pragma: allowlist secret
    with pytest.raises(TrackiwiError) as excinfo:
        InfluxWriter(cfg, opener=opener).write(["m f=1.0 1"])
    message = str(excinfo.value)
    assert "tok-secret" not in message
    assert "hunter2" not in message
    assert "dTpw" not in message
    assert "<redacted>" in message


def test_only_write_endpoints_receive_posts():
    """REQ_INFLUX_WRITE_SCOPE: writes go only to /api/v2/write or /write."""
    for cfg, path in (
        (_cfg(), "/api/v2/write"),
        (_cfg(version=1, database="d", token=None), "/write"),
    ):
        opener = FakeOpener(FakeResponse(b"", status=204))
        InfluxWriter(cfg, opener=opener).write(["trackiwi_position,tracker_id=1 lat=1.0 1"])
        assert [(r.method, urllib.parse.urlsplit(r.full_url).path) for r in opener.calls] == [
            ("POST", path)
        ]


def test_target_key_distinguishes_targets():
    a = InfluxWriter(_cfg(), opener=FakeOpener()).target_key()
    b = InfluxWriter(_cfg(bucket="other"), opener=FakeOpener()).target_key()
    c = InfluxWriter(
        _cfg(version=1, database="trackiwi", token=None), opener=FakeOpener()
    ).target_key()
    assert len({a, b, c}) == 3
    assert "tok-secret" not in a  # pragma: allowlist secret


def test_check_v2_bucket_found():
    body = json.dumps({"buckets": [{"name": "trackiwi"}]}).encode()
    opener = FakeOpener(FakeResponse(body, status=200))
    ok, lines = InfluxWriter(_cfg(), opener=opener).check()
    assert ok
    assert any("bucket 'trackiwi' found" in line for line in lines)
    assert all(r.method == "GET" for r in opener.calls)


def test_check_v2_bucket_missing():
    opener = FakeOpener(FakeResponse(json.dumps({"buckets": []}).encode(), status=200))
    ok, lines = InfluxWriter(_cfg(), opener=opener).check()
    assert not ok
    assert any("not found" in line for line in lines)


def test_check_v2_write_only_token_is_ok():
    opener = FakeOpener(FakeResponse(b"", status=403))
    ok, lines = InfluxWriter(_cfg(), opener=opener).check()
    assert ok
    assert any("not verifiable" in line for line in lines)


def test_check_v2_rejected_token():
    opener = FakeOpener(FakeResponse(b"", status=401))
    ok, lines = InfluxWriter(_cfg(), opener=opener).check()
    assert not ok
    assert any("rejected" in line for line in lines)


def test_check_v1_database_present():
    body = json.dumps(
        {"results": [{"series": [{"values": [["_internal"], ["trackiwi"]]}]}]}
    ).encode()
    opener = FakeOpener(FakeResponse(body, status=200))
    ok, lines = InfluxWriter(
        _cfg(version=1, database="trackiwi", token=None), opener=opener
    ).check()
    assert ok
    assert any("database 'trackiwi' found" in line for line in lines)
