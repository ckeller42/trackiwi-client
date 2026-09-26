"""InfluxDB writer: version detection, exact write requests, errors, redaction."""

import base64
import email.message
import gzip
import http.client
import io
import json
import urllib.error
import urllib.parse
import urllib.request
import urllib.response
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


# --- Redirects are errors, never acknowledgements (final review C1) ---------


_LOGIN = (
    "https://sso.example.invalid/login?next=/api/v2/write&t=tok-secret"  # pragma: allowlist secret
)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_redirect_on_write_is_an_error_not_an_ack(status):
    """A 3xx answer (an SSO gate, a proxy) must never count as a write acknowledgement."""
    opener = FakeOpener(
        FakeResponse(b"<html>login</html>", status=status, headers={"Location": _LOGIN})
    )
    with pytest.raises(TrackiwiError, match="redirect") as excinfo:
        InfluxWriter(_cfg(), opener=opener).write(["m f=1.0 1"])
    message = str(excinfo.value)
    assert "sso.example.invalid/login" in message
    assert "set url" in message
    assert "tok-secret" not in message
    assert len(opener.calls) == 1


def test_a_raised_redirect_http_error_is_an_error():
    """What the no-redirect opener really produces: an `HTTPError` carrying the 3xx."""
    headers = email.message.Message()
    headers["Location"] = "https://sso.example.invalid/login"
    error = urllib.error.HTTPError(
        "http://influx.example.invalid:8086/api/v2/write", 302, "Found", headers, io.BytesIO(b"")
    )
    with pytest.raises(TrackiwiError, match="redirect"):
        InfluxWriter(_cfg(), opener=FakeOpener(error)).write(["m f=1.0 1"])


def test_a_redirect_on_ping_is_an_error():
    opener = FakeOpener(FakeResponse(b"", status=301, headers={"Location": _LOGIN}))
    with pytest.raises(TrackiwiError, match="redirect"):
        InfluxWriter(_cfg(version=None), opener=opener).detect_version()


def test_a_redirect_on_check_is_an_error():
    opener = FakeOpener(FakeResponse(b"<html/>", status=302, headers={"Location": _LOGIN}))
    with pytest.raises(TrackiwiError, match="redirect"):
        InfluxWriter(_cfg(), opener=opener).check()


class _Response(urllib.response.addinfourl):
    """`addinfourl` plus the `msg` reason phrase `HTTPErrorProcessor` reads."""

    def __init__(self, body, headers, url, code):
        super().__init__(io.BytesIO(body), headers, url, code)
        self.msg = "Found" if code == 302 else "OK"


class _RedirectingHTTP(urllib.request.BaseHandler):
    """Offline stand-in for a proxy: 302 to a login page, which then answers 200.

    `handler_order` below the stock `HTTPHandler` (500) makes the opener ask
    this handler first, so no socket is ever opened. It records what reached
    it, which is exactly what a real server would have seen.
    """

    handler_order = 100

    def __init__(self):
        self.seen = []

    def http_open(self, req):
        self.seen.append((req.get_method(), req.full_url, req.get_header("Authorization")))
        headers = http.client.HTTPMessage()
        code = 200
        if len(self.seen) == 1:
            headers["Location"] = "http://sso.example.invalid/login"
            code = 302
        return _Response(b"<html/>", headers, req.full_url, code)


def _default_director(writer):
    # The default opener is the bound `open` of a per-writer `OpenerDirector`.
    director = getattr(writer.opener, "__self__", None)
    assert isinstance(director, urllib.request.OpenerDirector), writer.opener
    return director


def test_default_opener_never_follows_redirects():
    """Real urllib, no network: the 302 surfaces as an error and nothing follows it."""
    writer = InfluxWriter(_cfg())
    fake = _RedirectingHTTP()
    _default_director(writer).add_handler(fake)
    with pytest.raises(TrackiwiError, match="redirect"):
        writer.write(["m f=1.0 1"])
    # One request only: the redirect was not followed, so the token went nowhere else.
    assert [(method, url.split("?")[0]) for method, url, _ in fake.seen] == [
        ("POST", "http://influx.example.invalid:8086/api/v2/write")
    ]


def test_each_writer_gets_its_own_director():
    """Adding a handler to one writer's opener must not leak into another's."""
    assert _default_director(InfluxWriter(_cfg())) is not _default_director(InfluxWriter(_cfg()))


# --- Transport failures outside URLError (final review I2) ------------------


class _BrokenRead(FakeResponse):
    """A response whose body read fails, as `getresponse()`/`read()` do for real."""

    def __init__(self, error):
        super().__init__(b"", status=204)
        self.error = error

    def read(self, *args):
        raise self.error


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("timed out"),
        ConnectionResetError(54, "Connection reset by peer"),
        http.client.RemoteDisconnected("Remote end closed connection without response"),
        _BrokenRead(TimeoutError("timed out")),
        _BrokenRead(http.client.IncompleteRead(b"")),
    ],
    ids=["timeout", "reset", "remote-disconnected", "read-timeout", "incomplete-read"],
)
def test_transport_failures_become_clear_errors(failure):
    opener = FakeOpener(failure)
    with pytest.raises(TrackiwiError, match="cannot reach InfluxDB at http://influx.example"):
        InfluxWriter(_cfg(), opener=opener).write(["m f=1.0 1"])


# --- A transport failure while reading an *error* body (review finding) -----
#
# `error.read()` inside `except urllib.error.HTTPError` used to run outside
# any `try` that converts `OSError`/`http.client.HTTPException` to a
# `TrackiwiError`, so a timeout or reset while reading a 500's or 401's body
# escaped as a bare `TimeoutError`/`ConnectionResetError` — a raw traceback,
# and the already-known HTTP status (which callers map to a specific message)
# was lost with it.


def _http_error_with_broken_body(status: int, error: Exception) -> urllib.error.HTTPError:
    class _BrokenBody(io.BytesIO):
        def read(self, *args: Any) -> bytes:
            raise error

    return urllib.error.HTTPError(
        "http://influx.example.invalid:8086/api/v2/write",
        status,
        "error",
        email.message.Message(),
        _BrokenBody(b""),
    )


def test_error_body_read_failure_keeps_the_500_status_not_a_bare_timeouterror():
    error = _http_error_with_broken_body(500, TimeoutError("timed out"))
    with pytest.raises(TrackiwiError, match="write failed \\(HTTP 500\\)"):
        InfluxWriter(_cfg(), opener=FakeOpener(error)).write(["m f=1.0 1"])


def test_error_body_read_failure_on_401_still_reports_rejected_token():
    error = _http_error_with_broken_body(401, ConnectionResetError(54, "reset"))
    with pytest.raises(TrackiwiError, match="rejected the token or credentials \\(HTTP 401\\)"):
        InfluxWriter(_cfg(), opener=FakeOpener(error)).write(["m f=1.0 1"])


# --- Points beyond the retention policy (final review I3) -------------------

_RETENTION_V2 = (
    b'{"code":"unprocessable entity","message":"failure writing points to database: '
    b'partial write: points beyond retention policy dropped=3"}'
)
_RETENTION_V1 = b'{"error":"partial write: points beyond retention policy dropped=3"}'


@pytest.mark.parametrize(
    ("version", "status", "body"),
    [(2, 422, _RETENTION_V2), (1, 400, _RETENTION_V1)],
    ids=["v2-422", "v1-400"],
)
def test_points_beyond_retention_are_acknowledged_with_a_warning(version, status, body, capsys):
    cfg = _cfg(version=version, database="trackiwi") if version == 1 else _cfg()
    opener = FakeOpener(FakeResponse(body, status=status))
    InfluxWriter(cfg, opener=opener).write(["m f=1.0 1"])  # returns: acknowledged
    err = capsys.readouterr().err
    assert "beyond retention policy dropped=3" in err
    assert "warning" in err


def test_other_422_still_fails():
    body = b'{"code":"unprocessable entity","message":"field type conflict"}'
    opener = FakeOpener(FakeResponse(body, status=422))
    with pytest.raises(TrackiwiError, match="field type conflict"):
        InfluxWriter(_cfg(), opener=opener).write(["m f=1.0 1"])
