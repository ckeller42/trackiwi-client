import email.message
import http.client
import io
import json
import os
import stat
import urllib.error

import pytest
from conftest import FakeOpener, FakeResponse

from trackiwi.client import WEBSITE, AuthError, Client, TrackiwiError, default_config_path


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def login_response():
    body = json.dumps(
        {"server": "https://api.example.invalid/", "token": "tok", "user": {"id": 42}}
    ).encode()
    return FakeResponse(body)


def test_login_stores_server_token_and_user():
    opener = FakeOpener(login_response())
    client = Client(opener=opener)
    user = client.login("a@example.invalid", "pw")
    assert user["id"] == 42
    assert client.api_base == "https://api.example.invalid"
    assert client.token == "tok"
    assert client.user_id == 42


def test_login_posts_email_and_password_to_the_website():
    opener = FakeOpener(login_response())
    Client(opener=opener).login("a@example.invalid", "pw")
    request = opener.calls[0]
    assert request.full_url == "https://www.trackiwi.com/api/login"
    assert json.loads(request.data) == {"email": "a@example.invalid", "password": "pw"}


def test_login_saves_config_owner_only():
    Client(opener=FakeOpener(login_response())).login("a@example.invalid", "pw")
    path = default_config_path()
    assert json.loads(path.read_text())["token"] == "tok"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


def test_load_restores_a_saved_session():
    Client(opener=FakeOpener(login_response())).login("a@example.invalid", "pw")
    restored = Client.load()
    assert restored.token == "tok"
    assert restored.authenticated is True


def test_load_without_config_is_unauthenticated():
    assert Client.load().authenticated is False


@pytest.mark.parametrize("status", [401, 412])
def test_bad_credentials_raise_autherror(status):
    opener = FakeOpener(FakeResponse(b'{"message":"nope"}', status=status))
    with pytest.raises(AuthError):
        Client(opener=opener).login("a@example.invalid", "pw")


def test_409_explains_account_needs_attention():
    opener = FakeOpener(FakeResponse(b"", status=409))
    with pytest.raises(TrackiwiError, match="attention"):
        Client(opener=opener).login("a@example.invalid", "pw")


def test_503_reports_maintenance():
    opener = FakeOpener(FakeResponse(b"", status=503))
    with pytest.raises(TrackiwiError, match="maintenance"):
        Client(opener=opener).login("a@example.invalid", "pw")


def test_logout_revokes_then_deletes_config():
    Client(opener=FakeOpener(login_response())).login("a@example.invalid", "pw")
    opener = FakeOpener(FakeResponse(b"", status=204))
    client = Client.load(opener=opener)
    revoked = client.logout()
    assert revoked is True
    assert opener.calls[0].get_method() == "DELETE"
    assert opener.calls[0].full_url == "https://api.example.invalid/api/v2/session"
    assert not default_config_path().exists()


def test_logout_clears_local_state_even_if_revoke_fails():
    Client(opener=FakeOpener(login_response())).login("a@example.invalid", "pw")
    opener = FakeOpener(FakeResponse(b"", status=500))
    revoked = Client.load(opener=opener).logout()
    assert revoked is False
    assert not default_config_path().exists()


def test_authenticated_requests_send_bearer_token():
    Client(opener=FakeOpener(login_response())).login("a@example.invalid", "pw")
    opener = FakeOpener(FakeResponse(b'{"token":"tok","user":{"id":42}}'))
    assert Client.load(opener=opener).session_ok() is True
    assert opener.calls[0].get_header("Authorization") == "Bearer tok"


def test_session_ok_is_false_when_rejected():
    Client(opener=FakeOpener(login_response())).login("a@example.invalid", "pw")
    opener = FakeOpener(FakeResponse(b"", status=401))
    assert Client.load(opener=opener).session_ok() is False


# --- Real urllib exceptions, not fake in-band statuses ------------------
#
# Real `urllib.request.urlopen` never *returns* a response for a non-2xx
# status — it *raises* `urllib.error.HTTPError`. The tests above cover
# `_check`'s status-code mapping using in-band `FakeResponse(status=...)`
# values, but that leaves `_request`'s exception-conversion branches
# (`HTTPError` -> status tuple, `URLError` -> `TrackiwiError`) untested.
# These tests exercise those branches with real exception instances.


def _http_error(status: int, body: bytes = b"") -> urllib.error.HTTPError:
    # `HTTPError`'s `hdrs` is an `email.message.Message`, not a bare dict; the
    # client only ever calls `dict(error.headers)` on it, so an empty Message is
    # the faithful stand-in.
    return urllib.error.HTTPError(
        f"{WEBSITE}/api/login", status, "error", email.message.Message(), io.BytesIO(body)
    )


def test_raised_http_error_401_is_converted_to_autherror():
    opener = FakeOpener(_http_error(401, b'{"message":"nope"}'))
    with pytest.raises(AuthError):
        Client(opener=opener).login("a@example.invalid", "pw")


def test_raised_http_error_503_reports_maintenance():
    opener = FakeOpener(_http_error(503))
    with pytest.raises(TrackiwiError, match="maintenance"):
        Client(opener=opener).login("a@example.invalid", "pw")


def test_raised_url_error_becomes_trackiwierror_network_error():
    opener = FakeOpener(urllib.error.URLError("boom"))
    with pytest.raises(TrackiwiError, match="network error"):
        Client(opener=opener).login("a@example.invalid", "pw")


def test_login_with_malformed_success_body_raises_trackiwierror():
    body = json.dumps({"token": "tok", "user": {"id": 42}}).encode()  # missing "server"
    opener = FakeOpener(FakeResponse(body))
    with pytest.raises(TrackiwiError, match="unexpected login response"):
        Client(opener=opener).login("a@example.invalid", "pw")


def test_session_ok_propagates_non_auth_errors():
    Client(opener=FakeOpener(login_response())).login("a@example.invalid", "pw")
    opener = FakeOpener(urllib.error.URLError("boom"))
    with pytest.raises(TrackiwiError, match="network error"):
        Client.load(opener=opener).session_ok()


# --- The API base must be https, wherever it came from ---


def test_a_plain_http_api_base_is_rejected():
    """An `http://` base would send `Authorization: Bearer <token>` in
    cleartext on every subsequent request."""
    with pytest.raises(TrackiwiError, match="https"):
        Client(api_base="http://api.example.invalid", token="tok")


def test_a_plain_http_server_in_the_login_response_is_rejected():
    body = json.dumps(
        {"server": "http://api.example.invalid", "token": "tok", "user": {"id": 42}}
    ).encode()
    with pytest.raises(TrackiwiError, match="https"):
        Client(opener=FakeOpener(FakeResponse(body))).login("a@example.invalid", "pw")
    assert not default_config_path().exists()


def test_a_saved_config_with_an_http_api_base_is_rejected():
    _write_config('{"api_base": "http://api.example.invalid", "token": "tok", "user_id": 1}')
    with pytest.raises(TrackiwiError, match="https"):
        Client.load()


# --- 0600 on every path, not only after a follow-up chmod ---


@pytest.fixture
def no_chmod_and_open_umask(monkeypatch):
    """Neutralise the post-hoc `os.chmod` and widen the umask.

    `save()` used to create the file at the umask default and narrow it
    afterwards, leaving a window in which another local process could open the
    token file (and keep the descriptor after the chmod). With `os.chmod`
    disabled, only a create-time mode can produce 0600, so this fixture makes
    the test discriminating rather than decorative.
    """
    monkeypatch.setattr(os, "chmod", lambda *args, **kwargs: None)
    previous = os.umask(0)
    try:
        yield
    finally:
        os.umask(previous)


def test_config_is_created_owner_only_without_relying_on_chmod(no_chmod_and_open_umask):
    Client(opener=FakeOpener(login_response())).login("a@example.invalid", "pw")
    path = default_config_path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


def test_load_self_heals_a_widened_config(tmp_path):
    """`Store.__enter__` chmods 0600 on every entry; `Client.load()` did not,
    so a config file widened after login (a backup restore, a `cp`, a dotfile
    manager) stayed world-readable forever and nothing noticed."""
    Client(opener=FakeOpener(login_response())).login("a@example.invalid", "pw")
    path = default_config_path()
    os.chmod(path, 0o644)

    assert Client.load().token == "tok"

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


# --- Boundary conversion: no exception class may escape as a raw traceback ---


def _write_config(text: str) -> None:
    path = default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_corrupt_config_becomes_a_trackiwierror():
    """A truncated config file used to raise `json.JSONDecodeError`, which
    `main()` does not catch — every single command died with a traceback."""
    _write_config('{"api_base": "https://api.example.invalid", "tok')
    with pytest.raises(TrackiwiError, match="config file is corrupt"):
        Client.load()


def test_config_that_is_not_an_object_becomes_a_trackiwierror():
    _write_config("[1, 2, 3]")
    with pytest.raises(TrackiwiError, match="config file is corrupt"):
        Client.load()


def test_non_json_login_response_becomes_a_trackiwierror_without_the_body():
    """A WAF or captive portal answering 200 with an HTML page used to raise
    `json.JSONDecodeError`. The converted message must not carry the body,
    which can echo back the request's own Authorization header."""
    body = b"<html>blocked: Authorization: Bearer super-secret-token</html>"
    opener = FakeOpener(FakeResponse(body))
    with pytest.raises(TrackiwiError) as excinfo:
        Client(opener=opener).login("a@example.invalid", "pw")
    message = str(excinfo.value)
    assert "unexpected response from trackiwi" in message
    assert "super-secret-token" not in message
    assert "html" not in message


def test_logout_clears_api_base_and_user_id_too():
    client = Client(opener=FakeOpener(login_response()))
    client.login("a@example.invalid", "pw")
    opener = FakeOpener(FakeResponse(b"", status=204))
    client = Client.load(opener=opener)
    client.logout()
    assert client.token is None
    assert client.api_base is None
    assert client.user_id is None


# --- A config this tool did not write must still fail cleanly (N-2, N-6) ---


@pytest.mark.parametrize("value", ["123", "true", '["https://x"]', '{"a": 1}'])
def test_a_non_string_api_base_becomes_a_trackiwierror(value):
    """`_require_https` calls `api_base.lower()`, so a non-string value raised
    an uncaught `AttributeError` — a raw traceback one line past the validation
    block added to prevent exactly that."""
    _write_config(f'{{"api_base": {value}, "token": "tok", "user_id": 1}}')
    with pytest.raises(TrackiwiError, match="config file is corrupt"):
        Client.load()


def test_a_null_api_base_still_loads_as_not_logged_in():
    _write_config('{"api_base": null, "token": "tok", "user_id": 1}')
    assert Client.load().authenticated is False


def test_an_unreadable_config_reports_the_error_rather_than_corruption():
    """An `OSError` (permission denied, a directory in the way) is not
    corruption, and collapsing it into the corrupt-config message dropped the
    errno that says what is actually wrong."""
    path = default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()
    with pytest.raises(TrackiwiError) as excinfo:
        Client.load()
    message = str(excinfo.value)
    assert "corrupt" not in message
    assert "Errno" in message
    assert "trackiwi login" in message


def test_forget_local_session_removes_the_config():
    _write_config('{"api_base": "http://api.example.invalid", "token": "tok", "user_id": 1}')
    assert Client.forget_local_session() is True
    assert not default_config_path().exists()
    assert Client.forget_local_session() is False


# --- Transport failures outside URLError (final review I2) ---
#
# urllib wraps only the *send* in `URLError`. `getresponse()` and `read()`
# raise raw `TimeoutError`, `ConnectionResetError` or an `http.client`
# exception, which escaped as a traceback and, inside `ingest`, skipped the
# push entirely.


class _BrokenRead(FakeResponse):
    """A response whose body read fails mid-transfer."""

    def __init__(self, error):
        super().__init__(b"", status=200)
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
def test_transport_failures_become_trackiwierror_network_error(failure):
    with pytest.raises(TrackiwiError, match="network error"):
        Client(opener=FakeOpener(failure)).login("a@example.invalid", "pw")


class _BrokenErrorBody(io.BytesIO):
    """An `HTTPError.fp` whose `read()` fails, as a real socket can mid-transfer."""

    def __init__(self, error):
        super().__init__(b"")
        self.error = error

    def read(self, *args):
        raise self.error


def test_error_body_read_failure_keeps_the_status_401_becomes_autherror():
    """`error.read()` inside `except HTTPError` used to run outside the `try`
    that converts transport failures, so a timeout/reset while reading the
    error *body* escaped as a bare exception and the already-known 401 —
    which `_check` maps to `AuthError` — was lost with it."""
    error = urllib.error.HTTPError(
        f"{WEBSITE}/api/login",
        401,
        "error",
        email.message.Message(),
        _BrokenErrorBody(TimeoutError("timed out")),
    )
    with pytest.raises(AuthError):
        Client(opener=FakeOpener(error)).login("a@example.invalid", "pw")


def test_bare_timeout_error_names_the_exception_type_not_an_empty_message():
    """A bare `TimeoutError()` (no message) is always truthy, so `f"...{error
    or type(error).__name__}"` picked the (empty-string) error over the type
    name, producing the empty message "network error: "."""
    with pytest.raises(TrackiwiError, match="network error: TimeoutError"):
        Client(opener=FakeOpener(TimeoutError())).login("a@example.invalid", "pw")


def test_transport_failure_on_an_authed_call_is_redacted():
    """The token-scrubbing wrapper in `_api` still sees the converted error."""
    c = Client(
        api_base="https://api.example.invalid",
        token="tok-abc",
        opener=FakeOpener(
            ConnectionResetError(54, "reset while sending tok-abc")  # pragma: allowlist secret
        ),
    )
    with pytest.raises(TrackiwiError, match="network error") as excinfo:
        c.trackers()
    assert "tok-abc" not in str(excinfo.value)
