import json
import urllib.request

import pytest
from conftest import FakeOpener, FakeResponse, RedirectingHTTP

from trackiwi.client import Client, TrackiwiError

PAGE_1 = b"1,7,1758000000,120,31.0,-41.0,12,0.0,0,0,-71,9,98,4120\n"
PAGE_2 = b"2,7,1758000060,120,31.1,-41.1,12,0.0,0,0,-71,9,98,4120\n"
MALFORMED_2 = b"2,7,1758000060,120,nan,-41.1,12,0.0,0,0,-71,9,98,4120\n"
MALFORMED_3 = b"3,7,1758000120,120,31.2\n"
PAGE_4 = b"4,7,1758000180,120,31.3,-41.3,12,0.0,0,0,-71,9,98,4120\n"


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


def client(*responses):
    return Client(
        api_base="https://api.example.invalid",
        token="tok",
        user_id=42,
        opener=FakeOpener(*responses),
    )


def test_sync_pages_until_empty():
    c = client(
        FakeResponse(PAGE_1, headers={"trackiwi-position-count": "2"}),
        FakeResponse(PAGE_2),
        FakeResponse(b""),
    )
    batches = list(c.sync())
    assert [rows[0][0] for rows, _, _ in batches] == [1, 2]


def test_first_call_requests_initial_sync_then_offsets():
    opener = FakeOpener(FakeResponse(PAGE_1), FakeResponse(b""))
    c = Client(api_base="https://api.example.invalid", token="tok", opener=opener)
    list(c.sync())
    assert json.loads(opener.calls[0].data) == {"initial_sync": True}
    assert json.loads(opener.calls[1].data) == {"offset": 1}


def test_resuming_from_an_offset_skips_initial_sync():
    opener = FakeOpener(FakeResponse(b""))
    c = Client(api_base="https://api.example.invalid", token="tok", opener=opener)
    list(c.sync(offset=99))
    assert json.loads(opener.calls[0].data) == {"offset": 99}


def test_total_comes_from_the_count_header():
    c = client(FakeResponse(PAGE_1, headers={"trackiwi-position-count": "7"}), FakeResponse(b""))
    _, _, total = next(iter(c.sync()))
    assert total == 7


def test_total_is_none_without_the_header():
    c = client(FakeResponse(PAGE_1), FakeResponse(b""))
    _, _, total = next(iter(c.sync()))
    assert total is None


def test_malformed_rows_are_counted_not_fatal():
    c = client(FakeResponse(PAGE_1 + b"broken,row\n"), FakeResponse(b""))
    _, skipped, _ = next(iter(c.sync()))
    assert skipped == 1


def test_sync_posts_to_the_api_base():
    opener = FakeOpener(FakeResponse(b""))
    client = Client(api_base="https://api.example.invalid", token="tok", opener=opener)
    list(client.sync())
    assert opener.calls[0].full_url == "https://api.example.invalid/api/v2/trackers/sync"


def test_trackers_accepts_a_bare_list():
    c = client(FakeResponse(b'[{"id":7,"name":"Bus"}]'))
    assert c.trackers()[0]["name"] == "Bus"


def test_trackers_accepts_a_data_envelope():
    c = client(FakeResponse(b'{"data":[{"id":7,"name":"Bus"}]}'))
    assert c.trackers()[0]["id"] == 7


def test_trackers_raises_on_an_unexpected_shape():
    c = client(FakeResponse(b'{"error":"nope"}'))
    with pytest.raises(TrackiwiError):
        c.trackers()


def _offsets(opener):
    return [json.loads(call.data) for call in opener.calls]


def test_a_malformed_newest_row_is_skipped_and_the_offset_moves_past_it():
    """Issue #18: the offset used to stop at the last *parsed* id, so the
    malformed row came back alone on the next page and made it "unparseable"."""
    opener = FakeOpener(FakeResponse(PAGE_1 + MALFORMED_2), FakeResponse(b""))
    c = Client(api_base="https://api.example.invalid", token="tok", opener=opener)
    batches = list(c.sync())
    assert [(len(rows), skipped) for rows, skipped, _ in batches] == [(1, 1)]
    assert _offsets(opener) == [{"initial_sync": True}, {"offset": 2}]


def test_the_next_run_resumes_past_a_malformed_newest_row():
    """The cache holds id 1, so the next run asks from 1 and gets the bad row alone."""
    opener = FakeOpener(FakeResponse(MALFORMED_2), FakeResponse(b""))
    c = Client(api_base="https://api.example.invalid", token="tok", opener=opener)
    assert list(c.sync(offset=1)) == [([], 1, None)]
    assert _offsets(opener) == [{"offset": 1}, {"offset": 2}]


def test_a_page_of_only_malformed_rows_advances_by_their_ids():
    opener = FakeOpener(FakeResponse(MALFORMED_2 + MALFORMED_3), FakeResponse(PAGE_4))
    opener.responses.append(FakeResponse(b""))
    c = Client(api_base="https://api.example.invalid", token="tok", opener=opener)
    batches = list(c.sync(offset=1))
    assert [(len(rows), skipped) for rows, skipped, _ in batches] == [(0, 2), (1, 0)]
    assert _offsets(opener) == [{"offset": 1}, {"offset": 3}, {"offset": 4}]


def test_all_malformed_page_raises_instead_of_stopping_silently():
    # Without a parseable id there is no way to compute the next offset, so
    # a page that is not empty but yields no id at all must fail loudly rather
    # than look like end-of-data (which would strand the sync forever).
    c = client(FakeResponse(b"broken,row\n"), FakeResponse(b""))
    with pytest.raises(TrackiwiError):
        list(c.sync())


def test_non_advancing_offset_raises_instead_of_looping_forever():
    # If the server ever repeats (or regresses) the highest id, computing
    # the next offset from it would never move and the loop would spin
    # forever re-requesting the same page.
    opener = FakeOpener(FakeResponse(PAGE_1), FakeResponse(PAGE_1))
    c = Client(api_base="https://api.example.invalid", token="tok", opener=opener)
    with pytest.raises(TrackiwiError):
        list(c.sync())


# --- Token redaction (spec section 7.3: "redact it in any debug output") ---
#
# `_check`'s fallback interpolates the first 200 bytes of the server's body
# into the message, which main() prints verbatim to stderr. Reverse proxies and
# API gateways do echo request details — including request headers — into
# error bodies, and that body is the only unbounded server-controlled string
# this client ever prints.


def test_bearer_header_echoed_in_an_error_body_is_redacted():
    body = b"<html>502 upstream refused: Authorization: Bearer super-secret-token</html>"
    c = client(FakeResponse(body, status=502))
    with pytest.raises(TrackiwiError) as excinfo:
        c.trackers()
    message = str(excinfo.value)
    assert "super-secret-token" not in message
    assert "<redacted>" in message


def test_token_and_basic_headers_echoed_in_an_error_body_are_redacted():
    c = client(FakeResponse(b"echo: Token tok-abc and Basic dTpw", status=502))
    with pytest.raises(TrackiwiError) as excinfo:
        c.trackers()
    assert "tok-abc" not in str(excinfo.value)
    assert "dTpw" not in str(excinfo.value)


def test_the_sessions_own_token_echoed_in_an_error_body_is_redacted():
    """The `Bearer` pattern does not catch a token echoed on its own, so the
    concrete `self.token` value is scrubbed as well."""
    c = Client(
        api_base="https://api.example.invalid",
        token="tok-secret-123",
        opener=FakeOpener(FakeResponse(b'{"error":"token tok-secret-123 rejected"}', status=400)),
    )
    with pytest.raises(TrackiwiError) as excinfo:
        c.trackers()
    message = str(excinfo.value)
    assert "tok-secret-123" not in message
    assert "<redacted>" in message


def test_non_json_trackers_response_becomes_a_trackiwierror_without_the_body():
    c = client(FakeResponse(b"<html>Authorization: Bearer super-secret-token</html>"))
    with pytest.raises(TrackiwiError) as excinfo:
        c.trackers()
    message = str(excinfo.value)
    assert "unexpected response from trackiwi" in message
    assert "super-secret-token" not in message


def test_total_header_lookup_is_case_insensitive():
    c = client(FakeResponse(PAGE_1, headers={"Trackiwi-Position-Count": "5"}), FakeResponse(b""))
    _, _, total = next(iter(c.sync()))
    assert total == 5


def test_non_numeric_count_header_is_ignored_not_fatal():
    c = client(FakeResponse(PAGE_1, headers={"trackiwi-position-count": "many"}), FakeResponse(b""))
    _, _, total = next(iter(c.sync()))
    assert total is None


# --- Redirects are never followed (issue #15) --------------------------------


def test_default_opener_never_follows_redirects():
    """Real urllib, no network: the 302 is an error and the token goes nowhere else."""
    c = Client(api_base="https://api.example.invalid", token="tok", user_id=42)
    fake = RedirectingHTTP()
    # The default opener is the bound `open` of a per-client `OpenerDirector`.
    director = getattr(c.opener, "__self__", None)
    assert isinstance(director, urllib.request.OpenerDirector), c.opener
    director.add_handler(fake)
    with pytest.raises(TrackiwiError, match="redirect"):
        list(c.sync())
    # One request only: the redirect target was never called.
    assert fake.seen == [("POST", "https://api.example.invalid/api/v2/trackers/sync", "Bearer tok")]


def test_a_redirect_is_not_read_as_no_more_data():
    """An injected opener that hands back a 3xx with an empty body must not end the sync."""
    c = client(FakeResponse(b"", status=302, headers={"Location": "http://other.example.invalid"}))
    with pytest.raises(TrackiwiError, match="redirect"):
        list(c.sync())
