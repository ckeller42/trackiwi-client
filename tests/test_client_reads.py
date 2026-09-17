"""The read-only list endpoints beyond `trackers`.

All five were recovered from the vendor's own app bundle and then verified
against a live account: every one answered HTTP 200 with a **bare JSON list**,
not a `{"data": [...]}` envelope. `markers` and `shares` were empty on the test
account, so their element shape is unverified and the client deliberately does
not model it.

Every fixture here is synthetic: invented ids, invented names, invented
coordinates. No value from a real account appears in this repository.
"""

import pytest
from conftest import FakeOpener, FakeResponse

from trackiwi.client import Client, TrackiwiError


def client(*responses):
    return Client(
        api_base="https://api.example.invalid",
        token="tok",
        user_id=42,
        opener=FakeOpener(*responses),
    )


#: `(method name, request path, a synthetic bare-list body)`. The bodies carry
#: the *verified key sets* with invented values.
ENDPOINTS = [
    (
        "tours",
        "/api/v2/tours",
        b'[{"color":"#112233","ended_at":"2026-01-02T03:04:05Z","id":11,'
        b'"name":"Testfahrt","started_at":"2026-01-01T03:04:05Z","tracker_id":7}]',
    ),
    ("markers", "/api/v2/markers", b'[{"id":21,"name":"Invented marker"}]'),
    (
        "marker_categories",
        "/api/v2/marker_categories",
        b'[{"color":"#445566","id":31,"name":"Invented category"}]',
    ),
    (
        "alarms",
        "/api/v2/alarms",
        b'[{"acknowledged":false,"alarm_type":"geofence",'
        b'"event":{"latitude":31.0,"longitude":-41.0},"id":41,'
        b'"inserted_at":"2026-01-03T04:05:06Z","tracker_id":7}]',
    ),
    ("shares", "/api/v2/shares", b'[{"id":51,"name":"Invented share"}]'),
]


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_a_read_only_endpoint_returns_the_bare_list(method, path, body):
    opener = FakeOpener(FakeResponse(body))
    c = Client(api_base="https://api.example.invalid", token="tok", opener=opener)
    records = getattr(c, method)()
    assert isinstance(records, list)
    assert len(records) == 1
    assert opener.calls[0].full_url == f"https://api.example.invalid{path}"
    assert opener.calls[0].get_method() == "GET"


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_a_read_only_endpoint_rejects_a_non_list_payload(method, path, body):
    """A dict, a string or a number is not a list of records.

    Returning it anyway would hand the caller something that crashes later,
    somewhere with no context left about which endpoint produced it.
    """
    c = client(FakeResponse(b'{"error":"nope"}'))
    with pytest.raises(TrackiwiError, match="unexpected"):
        getattr(c, method)()


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_a_read_only_endpoint_rejects_a_scalar_payload(method, path, body):
    c = client(FakeResponse(b'"not a list"'))
    with pytest.raises(TrackiwiError, match="unexpected"):
        getattr(c, method)()


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_a_read_only_endpoint_names_itself_in_its_error(method, path, body):
    """The message has to say *which* read failed.

    Six methods share one helper, so a generic "unexpected response" would be
    ambiguous in exactly the situation it is meant to diagnose.
    """
    c = client(FakeResponse(b'{"error":"nope"}'))
    with pytest.raises(TrackiwiError) as excinfo:
        getattr(c, method)()
    assert method.replace("_", " ") in str(excinfo.value)


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_a_read_only_endpoint_propagates_an_auth_failure(method, path, body):
    from trackiwi.client import AuthError

    c = client(FakeResponse(b"", status=401))
    with pytest.raises(AuthError):
        getattr(c, method)()


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_a_read_only_endpoint_redacts_a_token_echoed_in_an_error_body(method, path, body):
    c = Client(
        api_base="https://api.example.invalid",
        token="tok-secret-123",
        opener=FakeOpener(FakeResponse(b'{"e":"token tok-secret-123 rejected"}', status=400)),
    )
    with pytest.raises(TrackiwiError) as excinfo:
        getattr(c, method)()
    assert "tok-secret-123" not in str(excinfo.value)


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_a_read_only_endpoint_hides_a_non_json_body(method, path, body):
    """A WAF or gateway answering 200 with HTML must not reach the message:
    such a page can echo the request's own Authorization header back."""
    c = client(FakeResponse(b"<html>Authorization: Bearer super-secret-token</html>"))
    with pytest.raises(TrackiwiError) as excinfo:
        getattr(c, method)()
    message = str(excinfo.value)
    assert "super-secret-token" not in message
    assert "html" not in message


def test_an_empty_list_is_a_valid_answer():
    """`markers` and `shares` were empty on the live account; that is data,
    not a failure."""
    c = client(FakeResponse(b"[]"))
    assert c.markers() == []


def test_none_of_the_read_only_methods_send_a_body():
    """Read-only is absolute: these are GETs and must carry no payload."""
    for method, _, body in ENDPOINTS:
        opener = FakeOpener(FakeResponse(body))
        c = Client(api_base="https://api.example.invalid", token="tok", opener=opener)
        getattr(c, method)()
        assert opener.calls[0].data is None


def test_trackers_still_accepts_both_envelopes_after_the_refactor():
    """`trackers` predates the shared helper and tolerated a `{"data": [...]}`
    wrapper because its envelope was unverified at the time. The live check
    observed a bare list, but the tolerance is kept rather than removed: it
    costs one branch and a single account on a single day is thin evidence for
    narrowing a parser."""
    assert client(FakeResponse(b'[{"id":7}]')).trackers()[0]["id"] == 7
    assert client(FakeResponse(b'{"data":[{"id":7}]}')).trackers()[0]["id"] == 7
