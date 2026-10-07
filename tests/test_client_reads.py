"""`Client.trackers`, the one read-only list endpoint. Fixtures are synthetic."""

import pytest
from conftest import FakeOpener, FakeResponse

from trackiwi.client import AuthError, Client, TrackiwiError


def client(*responses, token="tok"):
    return Client(
        api_base="https://api.example.invalid",
        token=token,
        user_id=42,
        opener=FakeOpener(*responses),
    )


def test_trackers_accepts_both_envelopes():
    assert client(FakeResponse(b'[{"id":7}]')).trackers()[0]["id"] == 7
    assert client(FakeResponse(b'{"data":[{"id":7}]}')).trackers()[0]["id"] == 7


def test_trackers_sends_a_bodyless_get():
    opener = FakeOpener(FakeResponse(b"[]"))
    c = Client(api_base="https://api.example.invalid", token="tok", opener=opener)
    assert c.trackers() == []
    assert opener.calls[0].full_url == "https://api.example.invalid/api/v2/trackers"
    assert opener.calls[0].get_method() == "GET"
    assert opener.calls[0].data is None


@pytest.mark.parametrize("body", [b'{"error":"nope"}', b'"not a list"'])
def test_trackers_rejects_a_non_list_payload(body):
    with pytest.raises(TrackiwiError, match="unexpected trackers response"):
        client(FakeResponse(body)).trackers()


def test_trackers_propagates_an_auth_failure():
    with pytest.raises(AuthError):
        client(FakeResponse(b"", status=401)).trackers()


def test_trackers_redacts_a_token_echoed_in_an_error_body():
    c = client(
        FakeResponse(b'{"e":"token tok-secret-123 rejected"}', status=400), token="tok-secret-123"
    )
    with pytest.raises(TrackiwiError) as excinfo:
        c.trackers()
    assert "tok-secret-123" not in str(excinfo.value)


def test_trackers_hides_a_non_json_body():
    """A gateway answering 200 with HTML can echo the Authorization header."""
    c = client(FakeResponse(b"<html>Authorization: Bearer super-secret-token</html>"))
    with pytest.raises(TrackiwiError) as excinfo:
        c.trackers()
    assert "super-secret-token" not in str(excinfo.value)
    assert "html" not in str(excinfo.value)
