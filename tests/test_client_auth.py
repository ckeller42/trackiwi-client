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
    client.logout()
    assert opener.calls[0].get_method() == "DELETE"
    assert opener.calls[0].full_url == "https://api.example.invalid/api/v2/session"
    assert not default_config_path().exists()


def test_logout_clears_local_state_even_if_revoke_fails():
    Client(opener=FakeOpener(login_response())).login("a@example.invalid", "pw")
    opener = FakeOpener(FakeResponse(b"", status=500))
    Client.load(opener=opener).logout()
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
    return urllib.error.HTTPError(f"{WEBSITE}/api/login", status, "error", {}, io.BytesIO(body))


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


def test_logout_clears_api_base_and_user_id_too():
    client = Client(opener=FakeOpener(login_response()))
    client.login("a@example.invalid", "pw")
    opener = FakeOpener(FakeResponse(b"", status=204))
    client = Client.load(opener=opener)
    client.logout()
    assert client.token is None
    assert client.api_base is None
    assert client.user_id is None
